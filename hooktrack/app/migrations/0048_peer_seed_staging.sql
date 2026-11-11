-- Seed accounts measured in several small batches (SociaVault credits arrive 50 at a time) before anything is published.
-- One row per sampled account, keyed by a hash of the handle so an account is never measured twice and no handle is stored.
-- The published benchmark (peer_benchmarks) is recomputed from these rows by `seed_benchmarks.py --publish`.
CREATE TABLE IF NOT EXISTS peer_seed_samples (
    platform      TEXT NOT NULL,
    handle_hash   TEXT NOT NULL,
    fmt           TEXT NOT NULL DEFAULT 'main',
    niche         TEXT NOT NULL DEFAULT 'general',
    followers     BIGINT NOT NULL,
    median_views  BIGINT NOT NULL,
    n_posts       INT NOT NULL DEFAULT 0,
    sampled_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (platform, handle_hash)
);
