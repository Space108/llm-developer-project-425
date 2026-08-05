"""PII masking at the YDB write boundary (not in the agent prompt)."""

from __future__ import annotations

import re

# Телефон: оставляем последние 2 цифры для оператора.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?7|8)[\s\-]?(?:\(?\d{3}\)?[\s\-]?)?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)
# Email
_EMAIL_RE = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
# Карта: 13–19 цифр с пробелами/дефисами
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ \-]*){13,19}(?!\d)")


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


def mask_pii(text: str) -> str:
    if not text:
        return text
    out = _PHONE_RE.sub(_mask_phone, text)
    out = _EMAIL_RE.sub(_mask_email, out)
    out = _CARD_RE.sub(_mask_card, out)
    return out
