-- The small tracked-accounts add-on (5 accounts) for people who need one more, not twenty-five.
ALTER TABLE addon_purchases DROP CONSTRAINT IF EXISTS addon_purchases_kind_check;
ALTER TABLE addon_purchases ADD CONSTRAINT addon_purchases_kind_check CHECK (kind IN ('connected', 'accounts', 'accounts5', 'credits'));
