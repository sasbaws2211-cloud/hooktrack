-- "What's working in your niche": recent short videos that did far better than their own channel's normal, found once a day
-- per niche from YouTube's public data. Shared by every workspace (it is not anyone's own data). Only numbers, the video's
-- own title and a link are kept; nothing is copied. Rows are deleted after a couple of weeks (YouTube limits how long its
-- data may be kept) and refreshed whenever the video is seen again.
CREATE TABLE IF NOT EXISTS discover_items (
    platform       TEXT NOT NULL DEFAULT 'youtube',
    video_id       TEXT NOT NULL,
    niche          TEXT NOT NULL,
    band           TEXT NOT NULL,             -- the creator's follower band (see app/peer.py)
    channel_id     TEXT NOT NULL,
    channel_title  TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    duration_s     REAL,
    published_at   TIMESTAMPTZ NOT NULL,
    views          BIGINT NOT NULL,
    channel_median BIGINT NOT NULL,            -- the creator's own usual views per short, from their other recent videos
    multiple       REAL NOT NULL,              -- views / channel_median
    subscribers    BIGINT,
    collected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (platform, video_id)
);
CREATE INDEX IF NOT EXISTS discover_items_feed_idx ON discover_items (niche, band, multiple DESC);
CREATE INDEX IF NOT EXISTS discover_items_collected_idx ON discover_items (collected_at);
