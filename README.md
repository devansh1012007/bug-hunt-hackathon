# Bug Hunt CTF: Fall Back platform

The event platform for **Fall Back**, a full-stack software debugging challenge
run by the Software Development Club at VIT Bhopal University (proposed date
6 December 2026). Teams receive a small full-stack application with planted
defects, reproduce the failures, trace root causes, repair the app, and report
everything through this platform. Verified CTF flags add capped bonus points.

Everything in this repository is a v1 one-shot build: the platform runs
locally today, is load-ready for a 50-participant rehearsal, and documents its
own limits honestly.

## Repository layout

| Path | What it is |
| --- | --- |
| `backend/` | FastAPI API (participant/staff), durable PostgreSQL job queue, worker, seed |
| `backend/tests/` | 37 pytest tests (idempotency, IDOR, ground-truth isolation, scoring, auth) |
| `backend/migrations/` | Ordered SQL migrations applied by `scripts/migrate.sh` |
| `frontend/` | React 19 + Vite + TypeScript + Tailwind 4 SPA ("Case File" design system) |
| `challenge-app/` | "Papyrus", the Node/Express challenge app with 10 planted bugs + 2 flags |
| `docs/` | The full documentation set (27+ documents, see below) |
| `scripts/` | Dev bootstrap, migrations, seed reset, E2E journey, load test |
| `.github/workflows/` | CI: backend tests + frontend build |

## Quick start (D0 local profile)

Requirements: Python 3.12+, Node 22+, a local PostgreSQL 16 or 17. The scripts
auto-detect binaries: `../tools/pg17/bin` if present, else `initdb`/`psql` on
PATH, else the newest `/usr/lib/postgresql/*/bin`; override with `PG_BIN`. The
dev cluster listens on 127.0.0.1:5434 (override `PGPORT`, and keep
`backend/.env` and `backend/tests` in sync).

**No password is needed anywhere in setup.** The dev cluster is initialized
with trust auth, so `pgadmin` has no password and localhost connections are
never password-checked (see `.env` for the role passwords used by the app
itself: `app_api_dev`, `gt_admin_dev`, `scheduler_dev`). If `psql` ever
prompts for a password, a different PostgreSQL cluster is listening on that
port; `scripts/dev_db.sh` detects this and tells you to pick another one
(`PGPORT=5435`).

```bash
# 1. database, roles, schemas (creates roles app_api / gt_admin / scheduler)
DB=bughunt scripts/dev_db.sh

# 2. migrations (plain versioned SQL, tracked in app.schema_migrations)
DB=bughunt PGUSER=pgadmin scripts/migrate.sh

# 3. Python dependencies (a venv is recommended; system Python may be managed)
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 4. seed demo data (dev-only credentials, printed at the end)
cd backend && .venv/bin/python seed.py --reset

# 5. API + worker + frontend
cd backend && .venv/bin/python -m uvicorn app.main:app --port 8100 &
cd backend && .venv/bin/python worker.py &
cd frontend && npm install && npm run dev   # proxies /v1 to :8100
```

Verification: `scripts/e2e_journey.py` runs 46 checks against a running stack
(last recorded run: 46/46 pass). `backend$ pytest` runs the 34-test suite
(last recorded run: 37/37 pass).

Demo accounts (dev only, password `Dev-only!Passw0rd!`): `organizer@`,
`reviewer1@`, `p1.team1@` ... `example.com`. The Papyrus challenge app runs
with `cd challenge-app && npm install && npm start` (port 4173).

## The 60-second security model

- PostgreSQL is the only durable state; three schemas separate trust domains:
  `app` (domain data), `gt` (ground truth, reachable only by the `gt_admin`
  role, never by the API's `app_api` role), `audit` (append-only hash chain).
- The server owns time, truth and verdicts. Verdicts are hidden until release.
- Submissions are idempotent (same key replays the original receipt even after
  the deadline; different payload under the same key is a 409).
- Scores live in an append-only ledger; corrections are reversal entries.
- One award per (stage, team, bug), enforced by a database constraint.

## Documentation map

Start here: `docs/ONBOARDING_GUIDE.md`, `docs/SYSTEM_ARCHITECTURE.md`,
`docs/DEPLOYMENT_RUNBOOK.md`. Event delivery: `docs/EVENT_OPERATIONS_PLAYBOOK.md`,
`docs/COMPETITION_RULES.md`, `docs/SCORING_MODEL.md`, the handbooks, the
privacy notice and terms. Engineering: `docs/API_REFERENCE.md`,
`docs/DATA_MODEL.md`, `docs/SECURITY_ARCHITECTURE.md`,
`docs/GROUND_TRUTH_ISOLATION.md`, `docs/TESTING_STRATEGY.md`,
`docs/PERFORMANCE_AND_CAPACITY.md`. Traceability:
`docs/SOURCE_OF_TRUTH_AND_TRACEABILITY.md`,
`docs/AUTONOMOUS_DECISION_LOG.md`, `docs/ONE_SHOT_ACCEPTANCE_CRITERIA.md`,
`docs/KNOWNS_LIMITATIONS_AND_FOLLOWUPS.md`. Design:
`docs/FRONTEND_DESIGN_DIRECTION.md`. AI tooling:
`docs/AI_TOOLING_RESEARCH_AND_DECISION.md`.

## Status and limits (honest)

- Working: auth with rotation + reuse detection, RBAC, events/stages lifecycle
  with readiness gate, challenge publishing, idempotent submissions, hidden
  feedback, deterministic matching, reviewer queue, scoring ledger, CTF flag
  verification with caps, leaderboard snapshots, disputes, audit chain, CSV
  exports, 46/46 E2E + 37/37 pytest, frontend build clean.
- Deliberately not in v1: participant code execution (no isolated runner;
  ADR-010 fallback), AI scoring (disabled by default, DEC-13), email delivery
  (accounts are admin-registered; initial passwords are claimed on first sign-in and resets are admin-only), SSE (5 s polling),
  MFA (step-up is password re-entry; WebAuthn documented as the production
  path). The 50-user capacity claim stays provisional until the load-test
  rehearsal passes (`scripts/load_test.py`,
  `docs/LOAD_TEST_REPORT.md`).
