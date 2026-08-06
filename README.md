### Hexlet tests and linter status:
[![Actions Status](https://github.com/Space108/llm-developer-project-425/actions/workflows/hexlet-check.yml/badge.svg)](https://github.com/Space108/llm-developer-project-425/actions)

## Архитектура

```
email → ibitsa7@yandex.ru
  → email-poller (IMAP pull ~1 мин)
  → Responses API: file_search + MCP
  → RAG (help-desk-kb) / ydb-tickets (guardrail + PII mask) → YDB
  → SMTP Reply
```

Pull: ответ обычно до ~60 сек. MCP: `require_approval: never`, запись в YDB только через CF. PII маскируется в CF, не в промпте.

**Trusted:** system prompt, конфиг MCP, ID индекса, код CF.  
**Untrusted:** текст письма / чата, фрагменты RAG — не подставлять в system prompt.

## Сдача

| | |
|---|---|
| Ящик | `ibitsa7@yandex.ru` (~60 сек, pull) |
| Агент | [help-desk](https://aistudio.yandex.ru/platform/folders/b1g744cuetoavqnusa7q/agents/fvt299r9451u2cn3m9r7) |
| Репо | https://github.com/Space108/llm-developer-project-425 |
| Индекс | `help-desk-kb` (`fvthf4vtg4l1e1vmca9g`) |
| CF | `email-poller`, `ydb-tickets`; workflow `daily-escalation` |

**Работает:** почта + RAG, MCP тикеты в YDB, injection/PII guardrail, модерация Studio, workflow эскалации.  
**Ограничения:** Telegram (сеть из YC); Seen на Help Desk → poller пропускает; self-mail с ящика Help Desk игнорируется.

**SA / секреты:** `ai-studio-sa` (functions, mcp, lockbox, llm, ydb). Секреты только в Lockbox / `.env` (не в git).

## Скриншоты E2E

Цепочка для проверяющего (папка [`docs/screenshots/`](docs/screenshots/)):

| Файл | Что видно |
|------|-----------|
| [01-reply-komandirovka.png](docs/screenshots/01-reply-komandirovka.png) | Ответ по почте из KB (`HR-командировки.md`) |
| [02-reply-wifi.png](docs/screenshots/02-reply-wifi.png) | Вне базы + тикет `c723294f-…` |
| [03-reply-create-ticket.png](docs/screenshots/03-reply-create-ticket.png) | `create-ticket` → `a397630a-…` |
| [04-ydb-list-my-tickets.png](docs/screenshots/04-ydb-list-my-tickets.png) | Записи в YDB (`list-my-tickets`) |
| [05-studio-file-search.png](docs/screenshots/05-studio-file-search.png) | Studio: File Search / трейс tools |
| [06-terminal-list-tickets.png](docs/screenshots/06-terminal-list-tickets.png) | Терминал: JSON тикетов |
| [07-studio-traces-tools.png](docs/screenshots/07-studio-traces-tools.png) | Studio: MCP + File Search |

## Что попробовать

На `ibitsa7@yandex.ru` с другого ящика (не открывать на Help Desk до ответа):

1. `как оформить командировку?` → ответ из KB (`HR-командировки.md`).
2. `как найти тур или оформить путёвку?` → ответ из KB (`HR-туры.md`).
3. `как оформить отпуск?` → ответ из KB (`HR-отпуск.md`).
4. `как подключить VPN?` → ответ из KB (`IT-VPN.md`).
5. `у меня отвалился Wi-Fi в переговорке X, что делать?` → вне базы; затем `не помогло, создай тикет категория bug` → `ticket_id` + `list-my-tickets`.

## Как развернуть (кратко)

1. Каталог YC + SA `ai-studio-sa` с ролями: `functions.functionInvoker`, `serverless.mcpGateways.invoker`, `lockbox.payloadViewer`, `ai.languageModels.user`, `ydb.editor`.
2. YDB Serverless → выполнить `src/ydb_tickets/schema.sql`.
3. Lockbox: `ydb-endpoint`, `ydb-database`, app-password почты.
4. CF `ydb-tickets` из `src/ydb_tickets/` (entrypoint `index.handle`) + секреты YDB; MCP gateway по `mcp-tools.yaml`.
5. Векторный индекс: `yandex-ai-studio vector-stores local docs/*.md --name help-desk-kb`.
6. CF `email-poller` из `src/` (entrypoint `email_poller.handle`) + IMAP/SMTP из Lockbox, env: `SEARCH_INDEX_ID`, `MCP_YDB_TICKETS_URL`, `YC_FOLDER_ID`, `AGENT_ID`.
7. Триггер timer `0/1 * * * ? *` → `email-poller`.
8. Workflow: `yc serverless workflow create --name daily-escalation --yaml-spec src/workflow.yaml` (+ SA, расписание).
9. Локально: скопировать `.env.example` → `.env`, подставить свои ID (пароли не коммитить).

Код: `src/email_poller.py`, `src/email_sender.py`, `src/workflow.yaml`, `src/ydb_tickets/{index.py,schema.sql,mcp-tools.yaml}`.
