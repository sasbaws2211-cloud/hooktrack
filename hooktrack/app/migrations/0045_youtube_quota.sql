-- How much of Google's free daily YouTube Data API allowance (10,000 units, resetting at midnight Pacific time) has been used,
-- so syncing can stop before the limit and resume by itself the next day.
CREATE TABLE IF NOT EXISTS youtube_quota (
    day   DATE PRIMARY KEY,
    units INT NOT NULL DEFAULT 0
);
