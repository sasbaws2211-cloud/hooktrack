-- A video can be marked "no speech" so it is never sent to the (paid) transcript service.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS transcript_skipped BOOLEAN NOT NULL DEFAULT FALSE;
