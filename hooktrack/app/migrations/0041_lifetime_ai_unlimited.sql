-- AI requests are not capped on lifetime licences (-1 = unlimited). Other limits are unchanged.
UPDATE plans SET max_ai_calls = -1 WHERE code IN ('ltd_1', 'ltd_2');
