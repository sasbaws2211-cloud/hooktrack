-- Auto-renew with a saved card (Paystack reusable authorization).
-- The authorization_code is stored encrypted and only exists if the customer ticked "renew automatically"
-- at checkout (payments.save_card records that consent per payment).
ALTER TABLE workspace_subscriptions
    ADD COLUMN IF NOT EXISTS auto_renew          BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS card_auth_enc       TEXT    NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS card_brand          TEXT    NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS card_last4          TEXT    NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS card_exp            TEXT    NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS card_email          TEXT    NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS renew_failures      INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_renew_attempt  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS renew_error         TEXT    NOT NULL DEFAULT '';

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS kind      TEXT    NOT NULL DEFAULT 'checkout',
    ADD COLUMN IF NOT EXISTS save_card BOOLEAN NOT NULL DEFAULT FALSE;
