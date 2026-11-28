-- Explicit publication completion marker. Stage status='released' is set
-- by the organizer before the asynchronous scoring/release job finishes; it
-- must not itself authorize participant result reads.
BEGIN;

ALTER TABLE app.stages
    ADD COLUMN IF NOT EXISTS results_published_at TIMESTAMPTZ;

COMMIT;
