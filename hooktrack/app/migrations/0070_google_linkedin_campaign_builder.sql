ALTER TABLE ad_campaign_drafts
    DROP CONSTRAINT ad_campaign_drafts_platform_check,
    ADD CONSTRAINT ad_campaign_drafts_platform_check
        CHECK (platform IN ('meta', 'tiktok', 'google', 'linkedin'));
