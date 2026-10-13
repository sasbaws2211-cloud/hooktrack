-- How a made card did against the hook it was made from, recorded when the user marks it analysed.
ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS verdict     TEXT NOT NULL DEFAULT '',     -- '' | beat | matched | fell_short
    ADD COLUMN IF NOT EXISTS analysed_at TIMESTAMPTZ;
