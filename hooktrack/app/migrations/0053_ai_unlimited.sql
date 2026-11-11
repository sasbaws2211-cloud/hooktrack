-- AI inside a workspace (hook ideas, weekly plans, reading screenshots, structuring a video, "ask your data") is background
-- functionality, not a chatbot people sit in, so it has no monthly request limit on any plan. Usage is still counted per workspace
-- and month so it can be watched. The free AI tools on the public pages keep their own daily limits (see app/freeai.py).
UPDATE plans SET max_ai_calls = -1 WHERE code IN ('free', 'solo', 'solo_year', 'pro', 'pro_year', 'agency', 'agency_year', 'lifetime');
