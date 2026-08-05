import base64
import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")


def handler(event, context):
    """
    Webhook от Telegram (через API Gateway).
    Ответ — в HTTP-теле method=sendMessage, без исходящих вызовов к Telegram.
    """
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_TOKEN is not set")
        return {"statusCode": 500, "body": "Token is not set"}

    try:
        body = event.get("body", "{}") if isinstance(event, dict) else "{}"
        if isinstance(event, dict) and event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8")
        update = json.loads(body) if isinstance(body, str) else (body or {})
    except Exception as e:
        logger.error(f"Failed to parse body: {e}")
        return {"statusCode": 400, "body": "Bad Request"}

    # API Gateway иногда кладёт JSON уже разобранным или вложенным
    if isinstance(update, dict) and "message" not in update and "body" in update:
        nested = update.get("body")
        if isinstance(nested, str):
            try:
                update = json.loads(nested)
            except Exception:
                pass
        elif isinstance(nested, dict):
            update = nested

    message = update.get("message") if isinstance(update, dict) else None
    if not message or "text" not in message:
        logger.info(f"Skip update keys={list(update.keys()) if isinstance(update, dict) else type(update)}")
        return {"statusCode": 200, "body": "OK"}

    chat_id = message["chat"]["id"]
    user_text = message["text"]
    reply_text = f"Яндекс Cloud принял твой запрос. Текст: {user_text}"
    logger.info(f"Reply to chat_id={chat_id}")

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "method": "sendMessage",
                "chat_id": chat_id,
                "text": reply_text,
            },
            ensure_ascii=False,
        ),
    }
