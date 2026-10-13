-- When a card reached Filmed (for the weekly pace). Set by a trigger so every way of moving a card is covered.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS filmed_at TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION posts_set_filmed_at() RETURNS trigger AS $$
BEGIN
    IF NEW.status = 'filmed' AND NEW.filmed_at IS NULL
       AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'filmed') THEN
        NEW.filmed_at := now();
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS posts_filmed_at ON posts;
CREATE TRIGGER posts_filmed_at BEFORE INSERT OR UPDATE OF status ON posts
    FOR EACH ROW EXECUTE FUNCTION posts_set_filmed_at();
