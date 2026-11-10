-- What a typical account of a similar size and niche gets per post, so a beginner with too few posts of their own
-- still gets an honest comparison. Only anonymous aggregates are stored here, never anyone's posts or handles.
--   source 'seed'  : computed from a sample of public accounts we collected ourselves
--   source 'users' : computed from HookTrack workspaces; only published for a group with at least 20 accounts
CREATE TABLE IF NOT EXISTS peer_benchmarks (
    platform    TEXT NOT NULL,             -- tiktok | instagram | youtube
    fmt         TEXT NOT NULL,             -- main | short
    niche       TEXT NOT NULL,             -- a bucket from app/peer.py, or 'general'
    band        TEXT NOT NULL,             -- follower band, or 'all'
    source      TEXT NOT NULL,
    n_accounts  INT  NOT NULL,
    n_posts     INT  NOT NULL DEFAULT 0,
    p25         BIGINT NOT NULL,           -- the middle account's normal views per post, and the quartiles around it
    median      BIGINT NOT NULL,
    p75         BIGINT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (platform, fmt, niche, band, source)
);
