"""PII masking at the YDB write boundary (not in the agent prompt).

Слой 1 — расширенные регулярки (телефон, email, карта, CVC, паспорт, пропуск).
Слой 2 — yandexgpt-lite как лёгкий «NER»: вычищает ФИО/адрес и прочее,
что регулярок не ловит. Fail-open: при сбое LLM остаётся результат слоя 1.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

# Телефон: оставляем последние 2 цифры для оператора.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?7|8)[\s\-]?(?:\(?\d{3}\)?[\s\-]?)?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)
# Email
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
# Карта: 13–19 цифр с пробелами/дефисами
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ \-]*){13,19}(?!\d)")
# CVC / CVV рядом с меткой
_CVC_RE = re.compile(
    r"(?i)(?:cvc|cvv|cvv2|код\s*безопасности)\s*[:\-–]?\s*\d{3,4}\b"
)
# Паспорт РФ: серия + номер (например 40 01 000000 / 4001 000000)
_PASSPORT_RE = re.compile(
    r"(?<!\d)(?:\d{2}\s?\d{2}\s?\d{6}|\d{4}\s?\d{6})(?!\d)"
)
# Пропуск / табельный — длинный числовой id с меткой
_PASS_ID_RE = re.compile(
    r"(?i)(?:номер\s+пропуска|пропуск|табельный(?:\s+номер)?)\s*[:\-–]?\s*\d{5,}"
)

# Эвристика: есть ли смысл звать LLM-редактор
_PII_HINT_RE = re.compile(
    r"(?i)("
    r"зовут|меня\s+зовут|фио|паспорт|прожива\w*|живу|адрес|улиц\w*|"
    r"квартир\w*|карта|cvc|cvv|пропуск|жен[аы]|сын|дочь|рожден"
    r")"
)

_REDACT_SYSTEM = (
    "Ты редактор персональных данных для help desk. "
    "Верни ТОЛЬКО текст обращения пользователя. "
    "Замени персональные данные плейсхолдерами: "
    "[ФИО], [АДРЕС], [ПАСПОРТ], [ТЕЛЕФОН], [EMAIL], [КАРТА], [CVC], [ПРОПУСК], [ПД]. "
    "Смысл проблемы (принтер, VPN, база и т.п.) сохрани. "
    "Не добавляй пояснений, кавычек и преамбулы. "
    "Если персональных данных нет — верни текст без изменений."
)


def _mask_phone(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) < 4:
        return "***"
    tail = digits[-2:]
    return f"+7 (***) ***-**-{tail}"


def _mask_email(match: re.Match[str]) -> str:
    local, domain = match.group(1), match.group(2)
    if len(local) <= 1:
        masked_local = "*"
    else:
        masked_local = local[0] + "***"
    return f"{masked_local}@{domain}"


def _mask_card(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) < 4:
        return "****"
    return f"**** **** **** {digits[-4:]}"


def _mask_regex(text: str) -> str:
    # CVC до карты: иначе «1111cvc 123» без границы слова
    out = _CVC_RE.sub("[CVC]", text)
    out = _PHONE_RE.sub(_mask_phone, out)
    out = _EMAIL_RE.sub(_mask_email, out)
    out = _CARD_RE.sub(_mask_card, out)
    out = _PASSPORT_RE.sub("[ПАСПОРТ]", out)
    out = _PASS_ID_RE.sub(lambda m: re.sub(r"\d", "*", m.group(0)), out)
    return out


def _iam_token() -> str | None:
    url = "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
    req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode()).get("access_token")
    except Exception:  # noqa: BLE001
        return None


def _llm_redact(text: str) -> str | None:
    """Лёгкая модель вместо полноценного NER. None = сбой → fail-open."""
    folder = os.environ.get("YC_FOLDER_ID", "")
    if not folder:
        return None
    token = _iam_token()
    if not token:
        return None

    model = os.environ.get(
        "PII_REDACT_MODEL_URI",
        f"gpt://{folder}/yandexgpt-lite/latest",
    )
    payload = {
        "modelUri": model,
        "completionOptions": {
            "stream": False,
            "temperature": 0.0,
            "maxTokens": 800,
        },
        "messages": [
            {"role": "system", "text": _REDACT_SYSTEM},
            {"role": "user", "text": (text or "")[:3000]},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-folder-id": folder,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        alts = data.get("result", {}).get("alternatives") or []
        if not alts:
            return None
        raw = (alts[0].get("message") or {}).get("text") or ""
        cleaned = raw.strip()
        if not cleaned:
            return None
        # защита от «отказа» модели пустым/служебным ответом
        if len(cleaned) < max(20, len(text) // 10) and "[" not in cleaned:
            return None
        return cleaned
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, KeyError):
        return None
    except Exception:  # noqa: BLE001
        return None


def mask_pii(text: str) -> str:
    if not text:
        return text
    out = _mask_regex(text)
    if _PII_HINT_RE.search(text):
        redacted = _llm_redact(out)
        if redacted:
            # ещё раз regex — на случай, если модель оставила цифры
            out = _mask_regex(redacted)
            print("PII_LLM_REDACT_OK")
        else:
            print("PII_LLM_REDACT_SKIP fail-open-regex")
    return out
