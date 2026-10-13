-- Extras a workspace bought on top of its plan. Paid through the same Paystack checkout as a plan
-- (payments.plan_code = 'addon:<kind>:<quantity>'); each purchase counts while expires_at is in the future.
CREATE TABLE IF NOT EXISTS addon_purchases (
    id           BIGSERIAL PRIMARY KEY,
    workspace_id INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('connected', 'accounts', 'credits')),
    units        INT  NOT NULL CHECK (units > 0),          -- what it adds to the limit
    reference    TEXT NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS addon_purchases_ws_idx ON addon_purchases (workspace_id, kind, expires_at);
