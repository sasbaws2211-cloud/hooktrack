-- The one-time lifetime plan, sold on hooktrack.site through the normal Paystack checkout.
-- Not offered until an admin switches it on (platform setting "lifetime_deal"), and never listed with the monthly/yearly plans.
-- Every limit is finite: a lifetime customer pays once but costs data credits, publishing and AI every month.
--   1 TikTok or Instagram account + up to 3 YouTube channels (read through Google's free API) = 4 tracked accounts in total,
--   1 publishing account, about 60 data credits and 200 AI requests a month, 1 team member. Extras are add-ons.
INSERT INTO plans (code, name, description, price_minor, currency, interval, max_accounts, max_members, max_ai_calls,
                   max_connected, max_credits, max_reports, active, position)
SELECT 'lifetime', 'HookTrack Lifetime',
       'Pay once. One TikTok or Instagram account, up to 3 YouTube channels and 1 publishing account, for as long as we run HookTrack.',
       14900, 'USD', 'lifetime', 4, 1, 200, 1, 60, 0, TRUE, 50
WHERE NOT EXISTS (SELECT 1 FROM plans WHERE code = 'lifetime');

-- Who bought it. This is what the "spots left" count comes from. A refund revokes the row, which frees the spot.
CREATE TABLE IF NOT EXISTS lifetime_grants (
    workspace_id   INTEGER PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    reference      TEXT NOT NULL,
    granted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at     TIMESTAMPTZ,
    revoked_reason TEXT NOT NULL DEFAULT ''
);
