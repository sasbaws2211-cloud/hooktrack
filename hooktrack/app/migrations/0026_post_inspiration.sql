-- Inspiration items attached to a card (or the item a card was made from): shown in the card's brief.
CREATE TABLE IF NOT EXISTS post_inspiration (
    post_id        INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    inspiration_id INT NOT NULL REFERENCES inspiration(id) ON DELETE CASCADE,
    workspace_id   INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, inspiration_id)
);
CREATE INDEX IF NOT EXISTS post_inspiration_insp_idx ON post_inspiration (inspiration_id);
