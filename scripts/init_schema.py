#!/usr/bin/env python3
"""Apply YDB schema from a .sql file via ydb-python-sdk.

Usage:
  export YDB_ENDPOINT=grpcs://ydb.serverless.yandexcloud.net:2135
  export YDB_DATABASE=/ru-central1/<cloud-id>/<db-id>
  export YC_IAM_TOKEN=$(yc iam create-token)
  python scripts/init_schema.py src/ydb_tickets/schema.sql
"""

from __future__ import annotations

import os
import sys

import ydb


def load_statements(path: str) -> list[str]:
    """Read SQL file; drop only full-line -- comments; keep inline comments."""
    lines: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.lstrip().startswith("--"):
                continue
            lines.append(line)
    text = "".join(lines)
    statements = [s.strip() for s in text.split(";") if s.strip()]
    return statements


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <schema.sql>", file=sys.stderr)
        return 1

    endpoint = os.environ["YDB_ENDPOINT"]
    database = os.environ["YDB_DATABASE"]
    token = os.environ["YC_IAM_TOKEN"]
    schema_path = sys.argv[1]

    statements = load_statements(schema_path)
    if not statements:
        print("No statements found", file=sys.stderr)
        return 1

    driver = ydb.Driver(
        endpoint=endpoint,
        database=database,
        credentials=ydb.AccessTokenCredentials(token),
    )
    driver.wait(timeout=10)

    pool = ydb.SessionPool(driver)
    try:
        for stmt in statements:
            print(f"Executing:\n{stmt}\n")

            def _run(session: ydb.Session, statement: str = stmt) -> None:
                session.execute_scheme(statement)

            pool.retry_operation_sync(_run)
        print("OK: schema applied")
        return 0
    finally:
        pool.stop()
        driver.stop()


if __name__ == "__main__":
    raise SystemExit(main())
