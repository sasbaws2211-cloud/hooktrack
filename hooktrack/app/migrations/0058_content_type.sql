-- Phase 1 of "complete content management": every post gets a content_type, so a post is only ever compared
-- against others of the SAME kind on the SAME account (a carousel's normal is not a Reel's normal). This
-- generalizes the one-off YouTube Shorts-vs-long-form split that already existed in logic.performance().
--
-- Values: short_video | long_video | image | carousel | text | mixed.
-- 'mixed' is a deliberate placeholder for platforms whose feed mixes real types we don't yet classify per post
-- (X today: a tweet can be text, image or video, and nothing recorded which). It behaves exactly like today's
-- single undivided per-account bucket, so backfilling it changes nothing about X's current scoring.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS content_type TEXT NOT NULL DEFAULT 'short_video';

-- Backfill existing rows from what's already known, matching today's real behavior exactly:
UPDATE posts p SET content_type = 'long_video'
  FROM accounts a WHERE a.id = p.account_id AND lower(a.platform) LIKE 'youtube%'
  AND p.duration_s IS NOT NULL AND p.duration_s > 180 AND p.content_type = 'short_video';

UPDATE posts p SET content_type = 'text'
  FROM accounts a WHERE a.id = p.account_id AND lower(a.platform) = 'linkedin' AND p.content_type = 'short_video';

UPDATE posts p SET content_type = 'mixed'
  FROM accounts a WHERE a.id = p.account_id AND lower(a.platform) IN ('x', 'twitter') AND p.content_type = 'short_video';

CREATE INDEX IF NOT EXISTS posts_account_content_type_idx ON posts (account_id, content_type);
