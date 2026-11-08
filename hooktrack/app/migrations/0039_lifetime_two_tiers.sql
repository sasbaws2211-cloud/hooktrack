-- Two lifetime levels, matching AppSumo code stacking (max 2 codes): 1 code = HookTrack Lifetime ($48.80),
-- 2 codes stacked = HookTrack Lifetime Plus ($97.60). Limits are monthly caps.
INSERT INTO plans (code, name, description, price_minor, currency, interval, max_accounts, max_members, max_ai_calls,
                   max_connected, max_credits, max_reports, active, position)
SELECT 'ltd_2', 'HookTrack Lifetime Plus', 'Stack a second code for a small team or a portfolio of accounts.', 9760, 'USD',
       'lifetime', 15, 5, 400, 3, 600, 1, FALSE, 11
WHERE NOT EXISTS (SELECT 1 FROM plans WHERE code = 'ltd_2');
