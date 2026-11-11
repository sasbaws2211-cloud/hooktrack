-- Likes, comments and shares for each Discover video. The platforms return them in the same responses already fetched, so they cost
-- nothing extra. NULL means "not known" (hidden likes, comments off, or collected before this column existed), and the card hides it.
ALTER TABLE discover_items ADD COLUMN IF NOT EXISTS likes BIGINT;
ALTER TABLE discover_items ADD COLUMN IF NOT EXISTS comments BIGINT;
ALTER TABLE discover_items ADD COLUMN IF NOT EXISTS shares BIGINT;
