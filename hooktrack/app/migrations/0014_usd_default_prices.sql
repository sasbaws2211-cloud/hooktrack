-- Seeded plans now list at $49.90 and $99.90 a month. Only rows still on the original seed price are changed,
-- so a price an operator already set is never overwritten.
UPDATE plans SET price_minor = 4990 WHERE code = 'pro'    AND price_minor = 2900 AND currency = 'USD';
UPDATE plans SET price_minor = 9990 WHERE code = 'agency' AND price_minor = 9900 AND currency = 'USD';
