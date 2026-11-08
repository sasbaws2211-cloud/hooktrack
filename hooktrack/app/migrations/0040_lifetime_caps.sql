-- Cap the two lifetime levels to what the data providers cost per month (SociaVault credits, Late/Zernio connected accounts).
-- A lifetime customer pays once but costs every month, so these are ceilings; raising a cap later is free, lowering is not.
UPDATE plans SET max_accounts = 3,  max_members = 2, max_ai_calls = 100, max_connected = 1, max_credits = 100 WHERE code = 'ltd_1';
UPDATE plans SET max_accounts = 10, max_members = 5, max_ai_calls = 400, max_connected = 2, max_credits = 300 WHERE code = 'ltd_2';
