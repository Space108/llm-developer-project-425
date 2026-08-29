import email
import imaplib
import json
import os
import re
import smtplib
import time
import urllib.error
import urllib.request
from email import policy
from email.mime.text import MIMEText
from email.utils import parseaddr


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


AGENT_INSTRUCTIONS = """
Ты — AI-агент корпоративной службы поддержки (Help Desk).

Перед ответом на вопрос по регламентам ВСЕГДА вызывай tool file_search.
Не выдумывай факты, которых нет в найденных документах.

Если file_search нашёл релевантный документ:
- дай краткий ответ (не больше 3 предложений цитаты);
- укажи название документа-источника.

Если релевантных документов нет или уверенность низкая:
- честно скажи, что в базе знаний ответа нет;
- предложи создать тикет через create-ticket.

Если пользователь просит создать тикет (или пишет «создай тикет», категория bug/docs/feature/access):
- ОБЯЗАТЕЛЬНО вызови tool create-ticket;
- user_id = email отправителя из контекста письма;
- поле text — ДОСЛОВНО текст обращения пользователя, без перефразирования и без «исправления» слов;
- в ответе пользователю явно укажи ticket_id из результата tool.

Если пользователь просит показать заявки — list-my-tickets.
После create-ticket желательно вызвать append-message (role=agent) с текстом твоего ответа.
""".strip()


def get_iam_token():
    url = "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
    req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode()).get("access_token")
    except Exception as e:  # noqa: BLE001
        print(f"Error fetching IAM token: {e}")
        return None


def _log_usage(result: dict) -> dict:
    usage = result.get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    inp = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    total = usage.get("total_tokens") or (inp + out)
    print(f"USAGE input_tokens={inp} output_tokens={out} total_tokens={total}")
    return {"input_tokens": int(inp or 0), "output_tokens": int(out or 0), "total_tokens": int(total or 0)}


def _extract_ticket_id(result: dict) -> str | None:
    output = result.get("output") or result.get("outputs") or []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "mcp_call":
            continue
        name = item.get("name") or ""
        if name not in ("create-ticket", "create_ticket"):
            continue
        raw_out = item.get("output")
        if isinstance(raw_out, dict) and raw_out.get("ticket_id"):
            return raw_out["ticket_id"]
        if isinstance(raw_out, str):
            try:
                parsed = json.loads(raw_out)
                if isinstance(parsed, dict) and parsed.get("ticket_id"):
                    return parsed["ticket_id"]
            except json.JSONDecodeError:
                m = re.search(r"[0-9a-fA-F-]{36}", raw_out)
                if m:
                    return m.group(0)
        args = item.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if isinstance(args, dict) and args.get("ticket_id"):
            return args["ticket_id"]
    # fallback: uuid in assistant text
    text = _extract_text(result)
    m = re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", text)
    return m.group(0) if m else None


def _extract_text(result: dict) -> str:
    """Достаём текст ответа из разных форматов Responses API."""
    if not isinstance(result, dict):
        return ""

    msg = result.get("message") or {}
    if isinstance(msg, dict) and msg.get("text"):
        return msg["text"]

    output = result.get("output") or result.get("outputs") or []
    chunks = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "file_search_call":
            print(
                f"FILE_SEARCH_CALL queries={item.get('queries')} "
                f"results={len(item.get('results') or item.get('output') or [])}"
            )
        if item_type == "mcp_call":
            print(
                f"MCP_CALL name={item.get('name')} "
                f"args={item.get('arguments')} output={item.get('output')}"
            )

        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    chunks.append(part["text"])
                elif isinstance(part, str):
                    chunks.append(part)
        elif isinstance(content, str) and content.strip():
            chunks.append(content)

        text = item.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text)
        elif isinstance(text, list):
            for part in text:
                if isinstance(part, dict) and part.get("text"):
                    chunks.append(part["text"])
                elif isinstance(part, str):
                    chunks.append(part)

    if chunks:
        return "\n".join(chunks).strip()

    return result.get("text") or ""


def _message_body(msg) -> str:
    if msg.is_multipart():
        plain = msg.get_body(preferencelist=("plain",))
        if plain:
            text = plain.get_content()
            if isinstance(text, str) and text.strip():
                return text
        html = msg.get_body(preferencelist=("html",))
        if html:
            raw = html.get_content()
            if isinstance(raw, str):
                return raw
        return ""
    content = msg.get_content()
    return content if isinstance(content, str) else str(content or "")


def _ydb_tickets_invoke(payload: dict, iam_token: str) -> str:
    fn_id = _required_env("YDB_TICKETS_FUNCTION_ID")
    url = f"https://functions.yandexcloud.net/{fn_id}?integration=raw"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {iam_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _append_agent_message(ticket_id: str, text: str, usage: dict, iam_token: str, latency_ms: int = 0) -> None:
    """Пишем ответ агента в messages с tokens_in/out из usage Responses API."""
    payload = {
        "action": "append-message",
        "ticket_id": ticket_id,
        "role": "agent",
        "text": text[:4000],
        "model": os.environ.get("YC_MODEL_URI", ""),
        "tokens_in": usage.get("input_tokens", 0),
        "tokens_out": usage.get("output_tokens", 0),
        "latency_ms": latency_ms,
    }
    try:
        body = _ydb_tickets_invoke(payload, iam_token)
        print(f"APPEND_MESSAGE_OK ticket_id={ticket_id} body={body[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"APPEND_MESSAGE_ERR ticket_id={ticket_id} err={e}")


def _fix_ticket_text(ticket_id: str, original_text: str, iam_token: str) -> None:
    """Перезаписываем text тикета дословным текстом письма (агент часто коверкает)."""
    payload = {
        "action": "update-ticket-text",
        "ticket_id": ticket_id,
        "text": (original_text or "")[:4000],
    }
    try:
        body = _ydb_tickets_invoke(payload, iam_token)
        print(f"FIX_TICKET_TEXT_OK ticket_id={ticket_id} body={body[:200]}")
    except Exception as e:  # noqa: BLE001
        print(f"FIX_TICKET_TEXT_ERR ticket_id={ticket_id} err={e}")


def ask_llm(text, agent_id, iam_token, sender_email=""):
    url = os.environ.get(
        "RESPONSES_API_URL",
        "https://rest-assistant.api.cloud.yandex.net/v1/responses",
    )
    folder_id = os.environ.get("YC_FOLDER_ID")
    search_index_id = _required_env("SEARCH_INDEX_ID")
    # По умолчанию MCP включён (шаг 9 E2E). Выключить: ENABLE_MCP=0
    enable_mcp = os.environ.get("ENABLE_MCP", "1").lower() not in ("0", "false", "no")
    mcp_url = os.environ.get("MCP_YDB_TICKETS_URL", "") if not enable_mcp else _required_env("MCP_YDB_TICKETS_URL")
    model = os.environ.get(
        "YC_MODEL_URI",
        f"gpt://{folder_id}/yandexgpt/latest" if folder_id else "",
    )

    headers = {
        "Authorization": f"Bearer {iam_token}",
        "Content-Type": "application/json",
    }
    if folder_id:
        headers["x-folder-id"] = folder_id

    user_text = text
    if sender_email:
        user_text = (
            f"Email отправителя (user_id для тикетов): {sender_email}\n\n"
            f"{text}"
        )

    tools = [
        {
            "type": "file_search",
            "vector_store_ids": [search_index_id],
            "max_num_results": 5,
        }
    ]
    if enable_mcp and mcp_url:
        tools.append(
            {
                "type": "mcp",
                "server_label": "ydb-tickets",
                "server_url": mcp_url,
                "require_approval": "never",
            }
        )

    # auto — чтобы агент мог и file_search, и create-ticket
    payload = {
        "instructions": AGENT_INSTRUCTIONS,
        "input": user_text,
        "tools": tools,
        "tool_choice": "auto",
    }
    if model:
        payload["model"] = model
    print(f"ASK_LLM agent_ref={agent_id} tools={[t.get('type') for t in tools]}")

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started_at = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=150) as response:
            result = json.loads(response.read().decode())
            latency_ms = int((time.monotonic() - started_at) * 1000)
            print(f"RESPONSES_KEYS={list(result.keys())} status={result.get('status')} latency_ms={latency_ms}")
            usage = _log_usage(result)
            reply = _extract_text(result) or "Извините, не удалось сформировать ответ."

            ticket_id = _extract_ticket_id(result)
            if ticket_id:
                print(f"TICKET_ID={ticket_id}")
                # исходный текст письма, не ответ модели
                _fix_ticket_text(ticket_id, text, iam_token)
                _append_agent_message(ticket_id, reply, usage, iam_token, latency_ms=latency_ms)

            return reply
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"LLM API HTTPError: {e.code} {body}")
        return "В данный момент я не могу ответить. Пожалуйста, обратитесь позже."
    except Exception as e:  # noqa: BLE001
        print(f"LLM API Error: {e}")
        return "В данный момент я не могу ответить. Пожалуйста, обратитесь позже."


def _send_reply(smtp_host, smtp_port, smtp_user, smtp_pass, sender_email, reply_subject, text, in_reply_to=None):
    reply_msg = MIMEText(text, _charset="utf-8")
    reply_msg["Subject"] = reply_subject
    reply_msg["From"] = smtp_user
    reply_msg["To"] = sender_email
    if in_reply_to:
        reply_msg["In-Reply-To"] = in_reply_to
        reply_msg["References"] = in_reply_to
    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(reply_msg)


def handle(event, context):
    imap_host = os.environ.get("IMAP_HOST", "imap.yandex.ru")
    imap_user = os.environ["IMAP_USER"]
    imap_pass = os.environ["IMAP_PASSWORD"]
    smtp_host = os.environ.get("SMTP_HOST", "smtp.yandex.ru")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER", imap_user)
    smtp_pass = os.environ["SMTP_PASSWORD"]
    agent_id = _required_env("AGENT_ID")
    mailbox_norm = imap_user.strip().lower()

    iam_token = get_iam_token()
    if not iam_token:
        return {"status": "error", "message": "Failed to get IAM token"}

    processed = 0
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(imap_host, 993)
        mail.login(imap_user, imap_pass)
        mail.select("INBOX")

        _, data = mail.search(None, "UNSEEN")
        mail_ids = data[0].split() if data[0] else []
        print(f"IMAP_OK unseen_count={len(mail_ids)}")

        for num in mail_ids[:1]:
            num_s = num.decode() if isinstance(num, (bytes, bytearray)) else str(num)
            sent_ok = False
            give_up = False
            is_retry = False
            sender_email = ""
            subject = ""
            try:
                _, msg_data = mail.fetch(num, "(FLAGS RFC822)")
                flags_raw = b""
                raw = None
                for part in msg_data:
                    if isinstance(part, tuple):
                        flags_raw, raw = part[0] or b"", part[1]
                if raw is None:
                    raise RuntimeError("empty fetch response")
                # \Flagged — стандартный IMAP-флаг, используем его как маркер
                # "уже пробовали и не получилось один раз". Если письмо падает
                # второй раз подряд (уже помечено), сдаёмся и снимаем его с
                # очереди — иначе оно блокировало бы весь ящик навсегда.
                is_retry = b"\\Flagged" in flags_raw

                msg = email.message_from_bytes(raw, policy=policy.default)

                _, sender_email = parseaddr(msg.get("From", ""))
                sender_norm = (sender_email or "").strip().lower()
                subject = msg.get("Subject") or "Тест"
                reply_subject = (
                    subject if subject.lower().startswith("re:") else f"Re: {subject}"
                )

                if not sender_norm:
                    print(f"SKIP empty_from num={num_s}")
                    mail.store(num, "+FLAGS", "\\Seen")
                    processed += 1
                    continue

                if sender_norm == mailbox_norm:
                    print(f"SKIP self_mail from={sender_email} subject={subject}")
                    mail.store(num, "+FLAGS", "\\Seen")
                    processed += 1
                    continue

                body = _message_body(msg)
                print(
                    f"GOT_UNSEEN=1 -> MSG num={num_s} retry={is_retry} "
                    f"from={sender_email} subject={subject} body_len={len(body or '')}"
                )

                llm_reply = ask_llm(body, agent_id, iam_token, sender_email=sender_email)
                print(f"AGENT_OK len={len(llm_reply)}")

                _send_reply(
                    smtp_host, smtp_port, smtp_user, smtp_pass,
                    sender_email, reply_subject, llm_reply,
                    in_reply_to=msg.get("Message-ID"),
                )
                print(f"SEND_OK to={sender_email}")
                sent_ok = True

            except Exception as e:  # noqa: BLE001
                give_up = is_retry
                print(f"Error processing message {num_s} (give_up={give_up}): {e}")
                if give_up and sender_email:
                    try:
                        _send_reply(
                            smtp_host, smtp_port, smtp_user, smtp_pass,
                            sender_email,
                            f"Re: {subject}" if subject else "Re: Тест",
                            "Извините, не удалось обработать ваше обращение из-за "
                            "технической ошибки. Пожалуйста, напишите нам ещё раз "
                            "или обратитесь к оператору напрямую.",
                        )
                        print(f"DEAD_LETTER_NOTIFY_OK to={sender_email}")
                    except Exception as notify_err:  # noqa: BLE001
                        print(f"DEAD_LETTER_NOTIFY_FAILED num={num_s} err={notify_err}")
            finally:
                if sent_ok:
                    mail.store(num, "+FLAGS", "\\Seen")
                    mail.store(num, "-FLAGS", "\\Flagged")
                    processed += 1
                elif give_up:
                    # Вторая попытка тоже упала — сдаёмся и снимаем письмо
                    # с очереди, чтобы оно не блокировало остальные.
                    print(f"DEAD_LETTER_GIVE_UP num={num_s}")
                    mail.store(num, "+FLAGS", "\\Seen")
                    processed += 1
                else:
                    mail.store(num, "+FLAGS", "\\Flagged")
                    print(f"KEEP_UNSEEN_RETRY num={num_s}")

    except Exception as e:  # noqa: BLE001
        print(f"IMAP Connection error: {e}")
        return {"status": "error", "message": str(e)}
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:  # noqa: BLE001, S110
                pass

    return {"processed": processed}
