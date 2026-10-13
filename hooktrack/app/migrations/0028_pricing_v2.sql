-- Pricing v2: priced on what costs money. Tracked accounts are nearly free (a daily sync is about 30 credits);
-- a connected publishing account costs $3-6 a month, so that is what the plans meter and the add-ons sell.
-- Existing price changes apply at each customer's next payment; nobody's paid period changes.

UPDATE plans SET name='Solo', description='For a creator with a few accounts.', price_minor=1900, currency='USD',
       interval='month', max_accounts=3, max_members=1, max_ai_calls=100, max_connected=1, max_credits=150,
       active=TRUE, position=1 WHERE code='solo';

UPDATE plans SET name='Pro', description='For a creator or small team running a portfolio.', price_minor=4900, currency='USD',
       interval='month', max_accounts=15, max_members=5, max_ai_calls=1000, max_connected=3, max_credits=600,
       active=TRUE, position=2 WHERE code='pro';

UPDATE plans SET name='Agency', description='For agencies and networks running many accounts.', price_minor=14900, currency='USD',
       interval='month', max_accounts=100, max_members=15, max_ai_calls=5000, max_connected=10, max_credits=4000,
       active=TRUE, position=3 WHERE code='agency';

-- yearly: two months free
INSERT INTO plans (code, name, description, price_minor, currency, interval, max_accounts, max_members, max_ai_calls,
                   max_connected, max_credits, active, position)
SELECT 'solo_year', 'Solo (yearly)', 'Solo, billed yearly: two months free.', 19000, 'USD', 'year', 3, 1, 100, 1, 150, TRUE, 4
WHERE NOT EXISTS (SELECT 1 FROM plans WHERE code='solo_year');
INSERT INTO plans (code, name, description, price_minor, currency, interval, max_accounts, max_members, max_ai_calls,
                   max_connected, max_credits, active, position)
SELECT 'pro_year', 'Pro (yearly)', 'Pro, billed yearly: two months free.', 49000, 'USD', 'year', 15, 5, 1000, 3, 600, TRUE, 5
WHERE NOT EXISTS (SELECT 1 FROM plans WHERE code='pro_year');
INSERT INTO plans (code, name, description, price_minor, currency, interval, max_accounts, max_members, max_ai_calls,
                   max_connected, max_credits, active, position)
SELECT 'agency_year', 'Agency (yearly)', 'Agency, billed yearly: two months free.', 149000, 'USD', 'year', 100, 15, 5000, 10, 4000, TRUE, 6
WHERE NOT EXISTS (SELECT 1 FROM plans WHERE code='agency_year');
