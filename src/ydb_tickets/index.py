import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import ydb

from moderation import classify
from pii import mask_pii


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def _driver() -> ydb.Driver:
    driver = ydb.Driver(
        endpoint=_env("YDB_ENDPOINT"),
        database=_env("YDB_DATABASE"),
        credentials=ydb.iam.MetadataUrlCredentials(),
    )
    driver.wait(timeout=10)
    return driver


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _safe_log(action: str, **fields: Any) -> None:
    """Логи только метаданные / уже маскированный текст — без сырого PII."""
    parts = [f"action={action}"]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    print(" ".join(parts))


def _parse_event(event: Any) -> dict:
    """Normalize invoke / API Gateway / MCP Hub payloads into a flat dict."""
    if event is None:
        return {}
    if isinstance(event, str):
        return json.loads(event) if event else {}
    if not isinstance(event, dict):
        return {}

    if event.get("httpMethod") or event.get("body") is not None:
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64

            body = base64.b64decode(body).decode("utf-8")
        if isinstance(body, str):
            return json.loads(body) if body else {}
        if isinstance(body, dict):
            return body
        return {}

    return event


def _resolve_action(payload: dict) -> str:
    """Dispatch action by explicit field or by argument keys (MCP Hub)."""
    action = payload.get("action")
    if action:
        return action

    if payload.get("action") == "update-ticket-text":
        return "update-ticket-text"
    if "ticket_id" in payload and "text" in payload and "role" not in payload and "user_id" not in payload:
        # явный patch текста без role → update
        if payload.get("fix_text") or payload.get("patch"):
            return "update-ticket-text"
    if "ticket_id" in payload and "role" in payload and "text" in payload:
        return "append-message"
    if "user_id" in payload and "category" in payload and "text" in payload:
        return "create-ticket"
    if "user_id" in payload:
        return "list-my-tickets"
    return "unknown"


def _guardrail(text: str, action: str) -> dict | None:
    """Блок injection до записи в YDB. off-topic — пропускаем с логом."""
    label, source = classify(text or "")
    _safe_log(
        action,
        stage="guardrail",
        label=label,
        source=source,
        text_len=len(text or ""),
    )
    if label == "injection":
        print("ALERT_INJECTION_BLOCKED")
        return {
            "error": "injection_blocked",
            "message": "Request blocked by security guardrail",
            "label": label,
            "source": source,
        }
    if label == "off-topic":
        _safe_log(action, stage="off-topic-allowed", note="ticket_allowed")
    return None


def create_ticket(pool: ydb.SessionPool, payload: dict) -> dict:
    ticket_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    now = _iso_now()
    user_id = payload["user_id"]
    category = payload.get("category", "bug")
    raw_text = payload.get("text", "")
    text = mask_pii(raw_text)
    status = payload.get("status", "open")

    blocked = _guardrail(raw_text, "create-ticket")
    if blocked:
        return blocked

    def callee(session: ydb.Session) -> None:
        q_ticket = session.prepare(
            """
            DECLARE $id AS Utf8;
            DECLARE $user_id AS Utf8;
            DECLARE $category AS Utf8;
            DECLARE $status AS Utf8;
            DECLARE $text AS Utf8;
            DECLARE $created_at AS Timestamp;
            DECLARE $updated_at AS Timestamp;

            UPSERT INTO tickets (id, user_id, category, status, text, created_at, updated_at)
            VALUES ($id, $user_id, $category, $status, $text, $created_at, $updated_at);
            """
        )
        q_msg = session.prepare(
            """
            DECLARE $id AS Utf8;
            DECLARE $ticket_id AS Utf8;
            DECLARE $role AS Utf8;
            DECLARE $text AS Utf8;
            DECLARE $model AS Utf8;
            DECLARE $tokens_in AS Uint64;
            DECLARE $tokens_out AS Uint64;
            DECLARE $latency_ms AS Uint32;
            DECLARE $created_at AS Timestamp;

            UPSERT INTO messages (
              id, ticket_id, role, text, model, tokens_in, tokens_out, latency_ms, created_at
            ) VALUES (
              $id, $ticket_id, $role, $text, $model, $tokens_in, $tokens_out, $latency_ms, $created_at
            );
            """
        )
        ts = int(time.time() * 1_000_000)
        session.transaction().execute(
            q_ticket,
            {
                "$id": ticket_id,
                "$user_id": user_id,
                "$category": category,
                "$status": status,
                "$text": text,
                "$created_at": ts,
                "$updated_at": ts,
            },
            commit_tx=True,
        )
        session.transaction().execute(
            q_msg,
            {
                "$id": message_id,
                "$ticket_id": ticket_id,
                "$role": "user",
                "$text": text,
                "$model": "",
                "$tokens_in": 0,
                "$tokens_out": 0,
                "$latency_ms": 0,
                "$created_at": ts,
            },
            commit_tx=True,
        )

    pool.retry_operation_sync(callee)
    _safe_log(
        "create-ticket",
        stage="ok",
        ticket_id=ticket_id,
        category=category,
        text_masked=text[:120],
    )
    return {"ticket_id": ticket_id, "created_at": now}


def update_ticket_text(pool: ydb.SessionPool, payload: dict) -> dict:
    """Исправить text (и опционально user_id) тикета — данные с границы письма."""
    ticket_id = payload["ticket_id"]
    raw_text = payload.get("text", "")
    text = mask_pii(raw_text)
    user_id = (payload.get("user_id") or "").strip()

    blocked = _guardrail(raw_text, "update-ticket-text")
    if blocked:
        return blocked

    def callee(session: ydb.Session) -> None:
        ts = int(time.time() * 1_000_000)
        if user_id:
            q_ticket = session.prepare(
                """
                DECLARE $id AS Utf8;
                DECLARE $text AS Utf8;
                DECLARE $user_id AS Utf8;
                DECLARE $updated_at AS Timestamp;

                UPDATE tickets
                SET text = $text, user_id = $user_id, updated_at = $updated_at
                WHERE id = $id;
                """
            )
            session.transaction().execute(
                q_ticket,
                {
                    "$id": ticket_id,
                    "$text": text,
                    "$user_id": user_id,
                    "$updated_at": ts,
                },
                commit_tx=True,
            )
        else:
            q_ticket = session.prepare(
                """
                DECLARE $id AS Utf8;
                DECLARE $text AS Utf8;
                DECLARE $updated_at AS Timestamp;

                UPDATE tickets
                SET text = $text, updated_at = $updated_at
                WHERE id = $id;
                """
            )
            session.transaction().execute(
                q_ticket,
                {"$id": ticket_id, "$text": text, "$updated_at": ts},
                commit_tx=True,
            )
        q_msg = session.prepare(
            """
            DECLARE $ticket_id AS Utf8;
            DECLARE $text AS Utf8;

            UPDATE messages
            SET text = $text
            WHERE ticket_id = $ticket_id AND role = 'user';
            """
        )
        session.transaction().execute(
            q_msg,
            {"$ticket_id": ticket_id, "$text": text},
            commit_tx=True,
        )

    pool.retry_operation_sync(callee)
    _safe_log(
        "update-ticket-text",
        stage="ok",
        ticket_id=ticket_id,
        user_id_set=bool(user_id),
        text_masked=text[:120],
    )
    return {"ticket_id": ticket_id, "text": text, "user_id": user_id or None, "ok": True}


def list_my_tickets(pool: ydb.SessionPool, payload: dict) -> list:
    user_id = payload["user_id"]
    _safe_log("list-my-tickets", stage="ok", user_id_len=len(user_id or ""))

    def callee(session: ydb.Session):
        query = session.prepare(
            """
            DECLARE $user_id AS Utf8;
            SELECT id, status, category, text, created_at
            FROM tickets VIEW tickets_by_user
            WHERE user_id = $user_id
            ORDER BY created_at DESC
            LIMIT 50;
            """
        )
        result_sets = session.transaction().execute(
            query,
            {"$user_id": user_id},
            commit_tx=True,
        )
        rows = []
        for row in result_sets[0].rows:
            created = row.created_at
            if hasattr(created, "isoformat"):
                created_at = created.isoformat()
            else:
                created_at = datetime.fromtimestamp(
                    int(created) / 1_000_000, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            rows.append(
                {
                    "id": row.id,
                    "status": row.status,
                    "category": row.category,
                    "text": row.text,
                    "created_at": created_at,
                }
            )
        return rows

    return pool.retry_operation_sync(callee)


def append_message(pool: ydb.SessionPool, payload: dict) -> dict:
    message_id = str(uuid.uuid4())
    ticket_id = payload["ticket_id"]
    role = payload["role"]
    raw_text = payload.get("text", "")
    text = mask_pii(raw_text)
    model = payload.get("model", "")
    tokens_in = int(payload.get("tokens_in", 0))
    tokens_out = int(payload.get("tokens_out", 0))
    latency_ms = int(payload.get("latency_ms", 0))

    if role == "user":
        blocked = _guardrail(raw_text, "append-message")
        if blocked:
            return blocked

    def callee(session: ydb.Session) -> None:
        q_msg = session.prepare(
            """
            DECLARE $id AS Utf8;
            DECLARE $ticket_id AS Utf8;
            DECLARE $role AS Utf8;
            DECLARE $text AS Utf8;
            DECLARE $model AS Utf8;
            DECLARE $tokens_in AS Uint64;
            DECLARE $tokens_out AS Uint64;
            DECLARE $latency_ms AS Uint32;
            DECLARE $created_at AS Timestamp;

            UPSERT INTO messages (
              id, ticket_id, role, text, model, tokens_in, tokens_out, latency_ms, created_at
            ) VALUES (
              $id, $ticket_id, $role, $text, $model, $tokens_in, $tokens_out, $latency_ms, $created_at
            );
            """
        )
        q_upd = session.prepare(
            """
            DECLARE $id AS Utf8;
            DECLARE $updated_at AS Timestamp;
            DECLARE $status AS Utf8;

            UPDATE tickets
            SET updated_at = $updated_at, status = $status
            WHERE id = $id;
            """
        )
        ts = int(time.time() * 1_000_000)
        status = "answered" if role == "agent" else "open"
        session.transaction().execute(
            q_msg,
            {
                "$id": message_id,
                "$ticket_id": ticket_id,
                "$role": role,
                "$text": text,
                "$model": model,
                "$tokens_in": tokens_in,
                "$tokens_out": tokens_out,
                "$latency_ms": latency_ms,
                "$created_at": ts,
            },
            commit_tx=True,
        )
        session.transaction().execute(
            q_upd,
            {"$id": ticket_id, "$updated_at": ts, "$status": status},
            commit_tx=True,
        )

    pool.retry_operation_sync(callee)
    _safe_log(
        "append-message",
        stage="ok",
        ticket_id=ticket_id,
        role=role,
        text_masked=text[:120],
    )
    return {"message_id": message_id, "ok": True}


def handle(event, context):
    try:
        payload = _parse_event(event)
        action = _resolve_action(payload)
        if action == "unknown":
            return {"error": f"unknown action: {payload.get('action')}"}

        driver = _driver()
        pool = ydb.SessionPool(driver)
        try:
            if action == "create-ticket":
                result = create_ticket(pool, payload)
            elif action == "list-my-tickets":
                result = list_my_tickets(pool, payload)
            elif action == "append-message":
                result = append_message(pool, payload)
            elif action == "update-ticket-text":
                result = update_ticket_text(pool, payload)
            else:
                result = {"error": f"unknown action: {action}"}
        finally:
            pool.stop()
            driver.stop()

        if isinstance(event, dict) and (event.get("httpMethod") or "requestContext" in event):
            status = 403 if isinstance(result, dict) and result.get("error") == "injection_blocked" else 200
            return {
                "statusCode": status,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(result, ensure_ascii=False),
            }
        return result
    except Exception as e:  # noqa: BLE001
        print(f"ydb-tickets error: {type(e).__name__}")
        err = {"error": str(e)}
        if isinstance(event, dict) and (event.get("httpMethod") or "requestContext" in event):
            return {
                "statusCode": 500,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(err, ensure_ascii=False),
            }
        return err
