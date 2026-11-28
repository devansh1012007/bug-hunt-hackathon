-- Explicit publication completion marker. Stage status='released' is set
-- by the organizer before the asynchronous scoring/release job finishes; it
-- must not itself authorize participant result reads.
--
-- No automatic backfill is performed for legacy released/final stages. The
-- previous release path could mark a stage released while scoring jobs were
-- still outstanding. Such rows remain hidden (marker NULL) until an organizer
-- retries publication through the new release barrier, which also builds a
-- fresh post-publication leaderboard snapshot.
BEGIN;

ALTER TABLE app.stages
    ADD COLUMN IF NOT EXISTS results_published_at TIMESTAMPTZ;

COMMIT;
