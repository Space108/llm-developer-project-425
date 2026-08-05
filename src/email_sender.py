import json
import os
import smtplib
from email.mime.text import MIMEText


def handler(event, context):
    """HTTP-обёртка SMTP для YaWL httpCall: {to, subject, body}."""
    try:
        if isinstance(event, dict) and isinstance(event.get("body"), (str, bytes, bytearray)):
            body = event["body"]
            if event.get("isBase64Encoded"):
                import base64

                body = base64.b64decode(body).decode("utf-8")
            payload = json.loads(body) if isinstance(body, str) else json.loads(body.decode("utf-8"))
        elif isinstance(event, dict):
            payload = event
        elif isinstance(event, (str, bytes, bytearray)):
            payload = json.loads(event)
        else:
            raise ValueError(f"Unsupported event type: {type(event)}")

        to_addr = payload["to"]
        subject = payload.get("subject", "Дайджест просроченных тикетов")
        text = payload.get("body", "")

        smtp_host = os.environ.get("SMTP_HOST", "smtp.yandex.ru")
        smtp_port = int(os.environ.get("SMTP_PORT", "465"))
        smtp_user = os.environ["SMTP_USER"]
        smtp_pass = os.environ["SMTP_PASSWORD"]

        msg = MIMEText(text, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = to_addr

        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        result = {"ok": True, "to": to_addr}
        if isinstance(event, dict) and (event.get("httpMethod") or "requestContext" in event):
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(result, ensure_ascii=False),
            }
        return result
    except Exception as e:  # noqa: BLE001
        err = {"ok": False, "error": str(e)}
        if isinstance(event, dict) and (event.get("httpMethod") or "requestContext" in event):
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(err, ensure_ascii=False),
            }
        return err
