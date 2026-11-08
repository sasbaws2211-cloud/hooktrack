-- One lifetime plan, one code: $48.80, no stacking. ltd_1 becomes "HookTrack Lifetime"; the unused tiers 2 and 3 go away
-- (only when nothing points at them, so an already-sold licence is never orphaned).

UPDATE plans SET name = 'HookTrack Lifetime',
                 description = 'Pay once, use it forever: find the hooks that work and scale them.',
                 price_minor = 4880, max_accounts = 5, max_members = 2, max_ai_calls = 150,
                 max_connected = 2, max_credits = 200, max_reports = 0
WHERE code = 'ltd_1';

UPDATE lifetime_licenses SET tier = 1, plan_code = 'ltd_1' WHERE plan_code IN ('ltd_2', 'ltd_3');
UPDATE workspace_subscriptions SET plan_code = 'ltd_1' WHERE plan_code IN ('ltd_2', 'ltd_3');
DELETE FROM plans WHERE code IN ('ltd_2', 'ltd_3')
  AND NOT EXISTS (SELECT 1 FROM workspace_subscriptions s WHERE s.plan_code = plans.code)
  AND NOT EXISTS (SELECT 1 FROM payments y WHERE y.plan_code = plans.code);
