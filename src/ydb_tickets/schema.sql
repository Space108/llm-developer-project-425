CREATE TABLE tickets (
  id           Utf8,        -- UUID
  user_id      Utf8,        -- email отправителя
  category     Utf8,        -- bug | docs | feature | access
  status       Utf8,        -- open | answered | escalated | closed
  text         Utf8,        -- текст обращения (после PII-маскирования)
  created_at   Timestamp,
  updated_at   Timestamp,
  PRIMARY KEY (id),
  INDEX tickets_by_user GLOBAL ON (user_id)   -- вторичный индекс для «мои заявки»
);

CREATE TABLE messages (
  id           Utf8,        -- UUID
  ticket_id    Utf8,        -- ссылка на tickets.id
  role         Utf8,        -- user | agent
  text         Utf8,        -- текст сообщения (после PII-маскирования)
  model        Utf8,        -- какая модель отвечала (если role=agent)
  tokens_in    Uint64,
  tokens_out   Uint64,
  latency_ms   Uint32,
  created_at   Timestamp,
  PRIMARY KEY (ticket_id, id)
);
