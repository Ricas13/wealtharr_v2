CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL UNIQUE,
  password_hash text NOT NULL,
  role text NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member')),
  notify_email boolean NOT NULL DEFAULT true,
  notify_discord boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash text PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
CREATE TABLE IF NOT EXISTS login_attempts (
  email text PRIMARY KEY, failures integer NOT NULL DEFAULT 0,
  locked_until timestamptz, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS portfolios (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name text NOT NULL,
  strategy_code text NOT NULL DEFAULT 'nine-sig' CHECK (strategy_code = 'nine-sig'),
  account_type text NOT NULL CHECK (account_type IN ('ISA','GIA','SIPP','OTHER')),
  currency text NOT NULL CHECK (currency IN ('GBP','USD')),
  equity_symbol text NOT NULL,
  safe_symbol text NOT NULL,
  monthly_contribution numeric(22,6) NOT NULL DEFAULT 0 CHECK (monthly_contribution >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  archived_at timestamptz,
  CONSTRAINT different_assets CHECK (equity_symbol <> safe_symbol)
);
CREATE INDEX IF NOT EXISTS portfolios_owner_idx ON portfolios(user_id,created_at);
CREATE TABLE IF NOT EXISTS ledger (
  id bigserial PRIMARY KEY,
  portfolio_id uuid NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('OPENING','DEPOSIT','WITHDRAWAL','BUY','SELL','DIVIDEND','FEE')),
  symbol text,
  quantity numeric(22,8) NOT NULL DEFAULT 0,
  unit_price numeric(22,8) NOT NULL DEFAULT 0,
  fees numeric(22,8) NOT NULL DEFAULT 0,
  amount numeric(22,8) NOT NULL DEFAULT 0,
  trade_date date NOT NULL,
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (quantity >= 0 AND unit_price >= 0 AND fees >= 0 AND amount >= 0),
  CHECK ((kind IN ('OPENING','BUY','SELL') AND symbol IS NOT NULL AND quantity > 0 AND unit_price > 0)
    OR (kind NOT IN ('OPENING','BUY','SELL') AND quantity = 0)),
  CHECK ((kind IN ('DEPOSIT','WITHDRAWAL','DIVIDEND','FEE') AND amount > 0)
    OR (kind IN ('OPENING','BUY','SELL') AND amount = 0))
);
CREATE INDEX IF NOT EXISTS ledger_portfolio_idx ON ledger(portfolio_id,trade_date,id);
CREATE TABLE IF NOT EXISTS price_quotes (
  symbol text PRIMARY KEY,
  price numeric(22,8) NOT NULL CHECK(price > 0),
  currency text NOT NULL,
  as_of date NOT NULL,
  fetched_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS snapshots (
  portfolio_id uuid NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
  as_of date NOT NULL,
  value numeric(22,6) NOT NULL,
  net_invested numeric(22,6) NOT NULL,
  cash numeric(22,6) NOT NULL,
  equity_value numeric(22,6) NOT NULL,
  safe_value numeric(22,6) NOT NULL,
  PRIMARY KEY(portfolio_id, as_of)
);
CREATE TABLE IF NOT EXISTS signals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  portfolio_id uuid NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK(kind IN ('QUARTERLY','THIRTY_DOWN')),
  signal_key text NOT NULL,
  as_of date NOT NULL,
  title text NOT NULL,
  explanation text NOT NULL,
  suggested_actions jsonb NOT NULL DEFAULT '[]',
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACKNOWLEDGED')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(portfolio_id,signal_key)
);
CREATE INDEX IF NOT EXISTS signals_portfolio_idx ON signals(portfolio_id,created_at DESC);
CREATE TABLE IF NOT EXISTS deliveries (
  id bigserial PRIMARY KEY,
  signal_id uuid NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  channel text NOT NULL CHECK(channel IN ('email','discord')),
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed')),
  attempts int NOT NULL DEFAULT 0,
  last_error text,
  sent_at timestamptz,
  UNIQUE(signal_id,user_id,channel)
);
CREATE TABLE IF NOT EXISTS app_settings (
  key text PRIMARY KEY, value text NOT NULL
);
INSERT INTO app_settings(key,value) VALUES ('nine_sig_enabled','true')
  ON CONFLICT(key) DO NOTHING;