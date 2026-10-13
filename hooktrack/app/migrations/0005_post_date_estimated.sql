-- A platform sometimes gives no publish time. Such posts are dated "first seen" and flagged, so a sync never
-- mistakes their whole lifetime view count for views gained today.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS date_estimated BOOLEAN NOT NULL DEFAULT FALSE;

-- YouTube rows created before this fix were all dated the day they were first synced
UPDATE posts p SET date_estimated = TRUE
FROM accounts a
WHERE a.id = p.account_id AND lower(a.platform) LIKE 'youtube%'
  AND p.external_id IS NOT NULL AND p.score_by = 'imported'
  AND p.post_date = (p.created_at AT TIME ZONE 'UTC')::date;
