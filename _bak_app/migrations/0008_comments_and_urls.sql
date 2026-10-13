-- The public URL of each synced post (the comment endpoints are addressed by URL, not by id)
ALTER TABLE posts ADD COLUMN IF NOT EXISTS url TEXT NOT NULL DEFAULT '';

-- what people wrote under a post, with what each comment says about intent (see app/intent.py)
CREATE TABLE IF NOT EXISTS post_comments (
    post_id      INT  NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    workspace_id INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    comment_id   TEXT NOT NULL,
    body         TEXT NOT NULL DEFAULT '',
    likes        INT,
    replies      INT,
    posted_at    TIMESTAMPTZ,
    kind         TEXT,                 -- buy | ask | more | NULL
    score        SMALLINT NOT NULL DEFAULT 0,
    matched      TEXT NOT NULL DEFAULT '',
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, comment_id)
);
CREATE INDEX IF NOT EXISTS post_comments_ws_kind_idx ON post_comments (workspace_id, kind) WHERE kind IS NOT NULL;

-- per-post roll-up, so lists never have to aggregate comments
ALTER TABLE posts ADD COLUMN IF NOT EXISTS comments_fetched    INT;          -- NULL = comments never read
ALTER TABLE posts ADD COLUMN IF NOT EXISTS comments_fetched_at TIMESTAMPTZ;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS buy_comments        INT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS ask_comments        INT;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS more_comments       INT;
