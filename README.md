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

## Сдача

| | |
|---|---|
| Ящик | `ibitsa7@yandex.ru` (~60 сек, pull) |
| Агент | [help-desk](https://aistudio.yandex.ru/platform/folders/b1g744cuetoavqnusa7q/agents/fvt299r9451u2cn3m9r7) |
| Репо | https://github.com/Space108/llm-developer-project-425 |
| Индекс | `help-desk-kb` (`fvthf4vtg4l1e1vmca9g`) |
| CF | `email-poller`, `ydb-tickets`; workflow `daily-escalation` |

**Работает:** почта + RAG, MCP тикеты в YDB, injection/PII guardrail, модерация Studio, workflow эскалации.  
**Не работает / ограничения:** Telegram (сеть из YC); Seen на Help Desk → poller пропускает; self-mail с ящика Help Desk игнорируется.

**SA / секреты:** `ai-studio-sa` (functions, mcp, lockbox, llm, ydb). Lockbox: ydb + пароль почты у CF. Trusted = промпт/конфиг MCP; untrusted = текст пользователя и RAG.

## Что попробовать

На `ibitsa7@yandex.ru` с другого ящика (не открывать на Help Desk до ответа):

1. `как оформить командировку?` → ответ из KB (`HR-командировки.md`).
2. `как найти тур или оформить путёвку?` → ответ из KB (`HR-туры.md`).
3. `как оформить отпуск?` → ответ из KB (`HR-отпуск.md`).
4. `как подключить VPN?` → ответ из KB (`IT-VPN.md`).
5. `у меня отвалился Wi-Fi в переговорке X, что делать?` → вне базы; затем `не помогло, создай тикет категория bug` → `ticket_id` в письме и `list-my-tickets`.

Логи: `GOT_UNSEEN` / `USAGE` / `MCP_CALL` / `SEND_OK` у `email-poller`; Traces у агента в AI Studio.

Шпаргалка к ревью (секреты, pull, MCP, Responses API): [`docs/shpargalka-review.pdf`](docs/shpargalka-review.pdf).
