-- No monthly cap on AI requests for any plan (-1 = unlimited). Other limits are unchanged.
UPDATE plans SET max_ai_calls = -1 WHERE code <> 'free';
