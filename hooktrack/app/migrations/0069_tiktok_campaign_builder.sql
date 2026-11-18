ALTER TABLE ad_campaign_drafts
    ADD COLUMN platform TEXT NOT NULL DEFAULT 'meta'
    CHECK (platform IN ('meta', 'tiktok'));
