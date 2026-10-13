-- Engagement beyond views/comments, as reported by the platform (NULL = the platform did not provide it).
-- saves/shares are the strongest "this made people want it" signals for the C in VSC.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS likes      BIGINT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS shares     BIGINT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS saves      BIGINT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS duration_s REAL;
