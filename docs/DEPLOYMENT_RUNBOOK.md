Status: describes the implementation as of 2026-11-26

# Deployment Runbook

Two profiles exist.

- D0 (local development) is fully scripted in this repository and is what all tests run against.
- D1 (free VM, public event) is specified in the master D1 guide but has NO deployment artifacts in this repo yet (no Dockerfile, compose file, cloudflared config or systemd units as of 2026-11-22).
- The D1 section below is the procedure to follow, with every not-yet-existing artifact marked as such.

## 1. D0: local development, step by step

Prerequisites:

- Python 3.12 (the scripts default to `python3`, override with `PYTHON=`).
- PostgreSQL 16 or 17 binaries, auto-detected: `$REPO_ROOT/../tools/pg17/bin` if present, else `initdb`/`psql` on PATH, else the newest `/usr/lib/postgresql/*/bin` (override with `PG_BIN=`).
- Node.js for the frontend.

Steps:

1. Database and roles: `bash scripts/dev_db.sh`.
   - Starts PostgreSQL on 127.0.0.1:5434 (initdb into `../tools/pgdata` on first run, log `../tools/pg.log`).
   - Creates database `bughunt` (override `DB=`) with schemas `app`, `gt`, `audit`.
   - Creates roles `app_api` / `gt_admin` / `scheduler` (dev passwords) and grants per the trust boundary, including default privileges for future tables.
2. Migrations: `bash scripts/migrate.sh`.
   - Runs as `PGUSER=pgadmin` (the database owner).
   - Creates `app.schema_migrations` and applies `backend/migrations/*.sql` in order.
3. Seed (dev only, known credentials): `cd backend && python seed.py` (add `--reset` to clear first).
   - Prints the seeded staff/participant credentials (`Dev-only!Passw0rd!`). The seed no longer creates or prints invite tokens.
   - Never run against a live deployment.
   - Shortcut: `bash scripts/run_dev.sh` performs steps 1-4 (add `--no-seed` / `--reset-seed`).
4. API + worker:
   - `cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8100`.
   - `cd backend && python worker.py`.
   - `run_dev.sh` nohups both into `logs/api.log` and `logs/worker.log`.
5. Frontend: `cd frontend && npm install && npm run dev`.
   - Vite on 5173 (strictPort), proxying `/v1` and `/health` to 8100 so the refresh cookie is first-party.
6. Verify:
   - `curl http://127.0.0.1:8100/health` must show `database_app: up` and `database_gt: up`.
   - `python scripts/e2e_journey.py` must exit 0 (46 checks).
   - `cd backend && python -m pytest` must show 34 passed.

Environment variables (all optional in dev, see `backend/app/config.py`):

- `DATABASE_URL_APP`, `DATABASE_URL_GT`, `JWT_SECRET`, `ENVIRONMENT`, `FRONTEND_ORIGIN`, `PROFILE`.
- Kill switches: `RUNNER_ENABLED` / `AI_ASSESSMENT_ENABLED` / `TELEMETRY_ENABLED` (all default false).
- A `.env` file next to the backend is read by pydantic-settings.

## 2. D1: OCI Always Free VM deployment (procedure; artifacts to be created)

Target shape (master D1 guide):

- One Oracle Cloud Always Free Ampere A1 VM (2 OCPU / 12 GB, Ubuntu ARM64).
- Docker Compose services; Cloudflare DNS + TLS + Tunnel; static SPA on Cloudflare Pages.
- PostgreSQL as the only durable store.
- NOT built yet in this repo; each artifact below must be created and committed before the rehearsal.

Procedure:

1. Provision: create the A1 instance in a home region with an Ubuntu ARM64 image, reserve the Always Free shape, open NO inbound ports (the Tunnel connects outbound).
2. Install Docker + Compose (ARM64); verify every image used supports linux/arm64 (master guide 13.3; CI should test this once images exist).
3. Compose service layout (to be authored; mirrors master guide 13.2 scaled to v1):
   - `postgres:17`, `api` (uvicorn, role `app_api`), `worker` (worker.py, uses the gt-capable engine for evaluation), `caddy` (TLS termination, body-size limits, security headers), `cloudflared` (Tunnel connector, token via env file).
   - The master guide separates participant/admin API processes and DB roles per process; with the current single FastAPI app, the two-engine design inside one process preserves the DB-role boundary, but a split process pair is the hardening follow-up.
4. Secrets:
   - Generate `JWT_SECRET` (32+ random bytes), DB passwords for `app_api` / `gt_admin` (replace the dev passwords), Cloudflare Tunnel token.
   - Keep them in the VM's env file (root-readable only) or a secrets manager; never commit them; never print them.
5. Database init on first boot:
   - Run the role/grant SQL (the `dev_db.sh` grant block, production passwords).
   - Run `migrate.sh` as the owner.
   - Create the tenant/event/challenge via the admin API (the dev seed with known passwords must NOT be used).
6. Cloudflare:
   - DNS record for the API hostname proxied through the Tunnel.
   - Disable Rocket Loader / Email Obfuscation / HTML minification on the hostname (NET-03).
   - Set scoped WAF rules with a skip for the submission path so attack-like bug reports are not 403'd (NET-02).
   - Exact CORS origin for the Pages domain in `FRONTEND_ORIGIN`.
7. Frontend:
   - `npm run build` in `frontend/`, publish `dist/` to Cloudflare Pages.
   - The SPA assumes same-origin `/v1`, so route a single hostname by path (Pages proxy or Tunnel route).
8. First-boot checks (before any users):
   - `/health` shows both databases up.
   - Re-run the pytest role tests against the production database to prove the grant boundary.
   - Admin pass-through: create event and teams -> register a participant from the admin setup page (email, name, team) -> verify the account has no password -> first sign-in with that registered email sets the initial password -> sign in again to verify password checking -> create challenge, upload the participant files, freeze, publish, enter ground truth via the GT endpoints (step-up required) -> submit one report and one flag end to end.
9. Backups before going live: see BACKUP_AND_RECOVERY.md; the restore drill is a readiness gate (`backup_restore`).

## Event-specific account lifecycle

- Participant accounts are created only by an administrator from the event's participant management screen. Registration requires an email address, a name, and a team in that event.
- No invitation token or activation URL is generated. The public `/auth/activate` route is not registered for this event.
- On first sign-in, the server checks that the email is already in the users table. An unknown email is rejected with the generic invalid-credentials response. If the registered user has no password hash, the submitted password becomes the initial password; later sign-ins verify it normally.
- This deliberately does not verify mailbox ownership for the first password claim. The event accepts the risk that someone who knows a registered email could claim its initial password before the intended participant. Admin registration and controlled distribution of the registered email are the trust boundary.
- Password recovery is not self-service. An administrator resets a participant's password from the admin console after a fresh step-up check; the reset revokes all of the target account's sessions and refresh tokens. The new password must be delivered to the participant through an approved private channel.

## 3. Event-day launch sequence

1. T-60: confirm `/health` green, worker running (`logs/worker.log` shows the handler list), job queue empty, no dead letters (`GET /v1/admin/system/health`).
2. T-60: freeze configuration.
   - `POST /v1/admin/events/{id}/config` with `freeze: true` (records `rule_set_hash`).
   - Stage config and rule_set_hash were fixed at stage creation.
   - After this, changes require a reason and a new revision (DAT-04).
3. T-30: verify the challenge is sealed and published, ground truth entered, assignment row exists (check `GET /v1/assignments` as a test participant).
4. T-15: check all readiness items (`GET .../readiness`).
   - Any `unchecked` item blocks going live.
   - Confirm the 50-user load test gate (readiness item `load_test_50`) is checked.
5. T-5: participants log in, accept rules, sit in the lobby (5 s polling).
   - Announcements post via the admin API if setup issues appear.
6. T-0: `POST /v1/admin/events/{id}/status {"status":"live"}` (readiness gate runs), then stage control `seal` -> `arm` -> `start`, each with a reason code.
   - `start` sets the stage window from `clock_timestamp()`; the client countdown derives from `/v1/state`.
7. During the round:
   - Watch `/v1/admin/events/{id}/live` (submission counts, queue age, dead letters, review backlog) and `/v1/admin/system/health`.
   - Pause/resume/extend/time-adjustments all require reason codes and are audited (TIM-05).
   - Per-user `time_adjustments` shift only that user's effective deadline.
8. T-end: `end` -> `evaluate` -> `release`.
   - `evaluate` runs penalties, code_review defaults and feedback release.
   - `release` publishes verdicts and the public leaderboard snapshot.
   - Reviewers should clear the queue before release; `release_feedback` applies the default 0.8 review quality (RELEASE_DEFAULT, disputable) to accepted-but-unreviewed reports.
9. Post-release:
   - Handle disputes via `/v1/admin/disputes` (upheld adjustments append ledger entries).
   - Verify the audit chain (`GET /v1/admin/audit/verify` intact).
   - Export results/submissions/ledger CSVs; archive the event status after the window.

## 4. Rollback and kill switches

- Kill switches are environment-level (`config.py`):
  - `runner_enabled=False` (Run panel absent; ADR-010 fallback).
  - `ai_assessment_enabled=False` (DEC-13).
  - `telemetry_enabled=False`.
  - Set them via env and restart the API process; `GET /v1/admin/system/health` reflects the current state. There is no hot-toggle endpoint by design.
- Stage transitions are forward-only: there is no un-release or un-end.
  - Containment is process-level: scores are ledger-derived, so corrections are appended disputes/adjustments (`DISPUTE_UPHELD`, `MANUAL`), never mutations.
  - Announce via a `critical` announcement and follow the scoring-correction runbook entry (master guide runbook list).
- A wrong stage start can be adjusted with `extend` (1..3600 s) or per-user `time_adjustments` with reason codes.
  - Pause/resume has the partially-implemented ends_at restoration caveat documented in SYSTEM_ARCHITECTURE.md Section 5.2.
  - Prefer `extend` after an outage rather than relying on `resume` time restoration.
- A compromised account: an admin can reset the account password from the admin console. This increments `token_version` and revokes all active sessions and refresh tokens. A reset is admin-only and requires a fresh password step-up.
- Full host loss (D1): restore per BACKUP_AND_RECOVERY.md on a fresh VM; the SPA on Cloudflare Pages can display a status notice; do not claim automatic failover (master guide: no fake failover claims).
- Change freeze: no code or config changes during an active round except the documented control actions (OPS-03).
  - If a fix is unavoidable, it goes through rehearsal on D0 first and is announced.
