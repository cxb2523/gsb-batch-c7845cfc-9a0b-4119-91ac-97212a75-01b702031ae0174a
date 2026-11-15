-- Minimal schema for raw transaction landing in Postgres.
-- Keep this intentionally small; dbt can build marts on top.

CREATE TABLE IF NOT EXISTS mpesa_transactions_raw (
  id BIGSERIAL PRIMARY KEY,
  transaction_id TEXT,
  phone_number TEXT,
  amount NUMERIC,
  account_reference TEXT,
  transaction_time TEXT,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  source TEXT NOT NULL,
  payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS mpesa_stk_transactions (
  checkout_request_id TEXT PRIMARY KEY,
  phone_number TEXT NOT NULL,
  amount NUMERIC NOT NULL,
  account_reference TEXT,
  status TEXT NOT NULL,
  initiated_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  result_code INTEGER,
  result_desc TEXT,
  description TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mpesa_transactions_raw_transaction_id
  ON mpesa_transactions_raw (transaction_id);

CREATE INDEX IF NOT EXISTS idx_mpesa_transactions_raw_phone_number
  ON mpesa_transactions_raw (phone_number);

CREATE INDEX IF NOT EXISTS idx_mpesa_transactions_raw_received_at
  ON mpesa_transactions_raw (received_at);

-- Backlog for Safaricom callbacks that could not be published to Kafka.
-- The webhook receiver writes here ("persist first, then deliver") so a
-- broker outage never drops a transaction; rows can be replayed via
-- POST /admin/backlog/<id>/retry. The receiver also creates this table
-- lazily on first use, so it is safe to apply this file independently.
CREATE TABLE IF NOT EXISTS webhook_callback_backlog (
  id BIGSERIAL PRIMARY KEY,
  callback_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  failure_reason TEXT,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  retry_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  last_attempt_at TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ,
  CONSTRAINT webhook_callback_backlog_status_check
    CHECK (status IN ('pending', 'delivered', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_webhook_callback_backlog_status_received
  ON webhook_callback_backlog (status, received_at DESC);
