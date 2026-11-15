-- Inbox: comments and DMs on a connected account (yours or a client's, read via Zernio/Late), with replies sent from here.
-- Separate from post_comments (which only scores buying intent on posts HookTrack already tracks, read-only, no replies).
-- Nothing here requires a social login of its own: it rides on the same OAuth connection used for publishing.

CREATE TABLE IF NOT EXISTS inbox_comments (
    id              SERIAL PRIMARY KEY,
    workspace_id    INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    account_id      INT  NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL,
    post_external_id TEXT NOT NULL,          -- the video's id on the platform, as Zernio reports it
    post_title      TEXT NOT NULL DEFAULT '',
    post_url        TEXT NOT NULL DEFAULT '',
    comment_id      TEXT NOT NULL,            -- Zernio's comment id
    parent_comment_id TEXT NOT NULL DEFAULT '',
    author_name     TEXT NOT NULL DEFAULT '',
    author_avatar   TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL DEFAULT '',
    like_count      INT,
    posted_at       TIMESTAMPTZ,
    replied_at      TIMESTAMPTZ,
    reply_body      TEXT NOT NULL DEFAULT '',
    hidden_at       TIMESTAMPTZ,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, comment_id)
);
CREATE INDEX IF NOT EXISTS inbox_comments_ws_idx ON inbox_comments (workspace_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS inbox_comments_unreplied_idx ON inbox_comments (account_id) WHERE replied_at IS NULL;

CREATE TABLE IF NOT EXISTS inbox_conversations (
    id                  SERIAL PRIMARY KEY,
    workspace_id        INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    account_id          INT  NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    platform            TEXT NOT NULL,
    conversation_id     TEXT NOT NULL,        -- Zernio's conversation id
    participant_name    TEXT NOT NULL DEFAULT '',
    participant_avatar  TEXT NOT NULL DEFAULT '',
    last_message_at     TIMESTAMPTZ,
    last_message_preview TEXT NOT NULL DEFAULT '',
    unread              BOOLEAN NOT NULL DEFAULT FALSE,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS inbox_conversations_ws_idx ON inbox_conversations (workspace_id, last_message_at DESC);

CREATE TABLE IF NOT EXISTS inbox_messages (
    id              SERIAL PRIMARY KEY,
    conversation_id INT  NOT NULL REFERENCES inbox_conversations(id) ON DELETE CASCADE,
    workspace_id    INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    message_id      TEXT NOT NULL,            -- Zernio's message id
    direction       TEXT NOT NULL,            -- 'in' | 'out'
    body            TEXT NOT NULL DEFAULT '',
    attachment_url  TEXT NOT NULL DEFAULT '',
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, message_id)
);
CREATE INDEX IF NOT EXISTS inbox_messages_conv_idx ON inbox_messages (conversation_id, sent_at);

-- Webhook delivery log, so a duplicate event (Zernio can retry up to ~51 hours) is dropped instead of double-applied.
CREATE TABLE IF NOT EXISTS inbox_webhook_events (
    event_id     TEXT PRIMARY KEY,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
