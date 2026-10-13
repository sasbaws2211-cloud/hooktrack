-- Tenant rows must belong to a workspace. Pre-multitenancy rows with a NULL workspace_id are claimed by the
-- first account that signs up, so a table is only tightened once it has no NULLs. The function is re-run
-- after that adoption (see auth.signup), so the constraint lands as soon as it can.
CREATE OR REPLACE FUNCTION ht_tighten_workspace_ids() RETURNS void AS $$
DECLARE t text; has_null boolean;
BEGIN
    FOREACH t IN ARRAY ARRAY['phones', 'accounts', 'daily_views', 'posts'] LOOP
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = t AND column_name = 'workspace_id' AND is_nullable = 'YES') THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE workspace_id IS NULL)', t) INTO has_null;
            IF NOT has_null THEN
                EXECUTE format('ALTER TABLE %I ALTER COLUMN workspace_id SET NOT NULL', t);
            ELSE
                RAISE NOTICE 'table % still has rows without a workspace; leaving workspace_id nullable', t;
            END IF;
        END IF;
    END LOOP;
END $$ LANGUAGE plpgsql;

SELECT ht_tighten_workspace_ids();
