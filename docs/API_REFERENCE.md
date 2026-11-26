Status: describes the implementation as of 2026-11-26

# API Reference

Base URL: same origin as the SPA, paths under `/v1` (plus the unversioned `GET /health`). Development OpenAPI/Swagger is served at `/v1/docs` and `/v1/openapi.json` only when `ENVIRONMENT=development` (`main.py`). Every response carries `X-Request-Id`; every error uses one envelope:

```json
{"error": {"code": "ROUND_ENDED", "message": "Time is up. ...", "request_id": "a1b2c3...", "details": {}}}
```

Codes and HTTP statuses come from the catalog in `backend/app/errors.py`. Known quirk: the catalog messages for `AUTH_RATE_LIMITED` and `SUBMISSION_COOLDOWN` contain a `{n}` placeholder that is NOT interpolated by `api_error_handler`; the real remaining-seconds values are in `details.n` (details keys: `n` for those codes, plus code-specific keys noted below). Likewise `INTERNAL_ERROR` interpolates the request id into the message server-side.

Authentication: `Authorization: Bearer <access token>` on everything except the endpoints marked "no auth" below. Roles: `participant`, `reviewer`, `organizer`, `admin`. `require_staff` accepts reviewer/organizer/admin; `require_organizer` accepts organizer/admin. Step-up: sensitive admin routes raise 403 `ADMIN_STEP_UP_REQUIRED` unless `POST /v1/auth/step-up` succeeded within the last 5 minutes for the caller's session (AUTH-09).

**Event account policy:** Participant accounts can be created only by an administrator through the event participant endpoint above. Staff accounts are created by `POST /v1/auth/register`. There are no invite tokens, activation routes, or self-service password-reset routes for this event. A registered account with no password claims its initial password on first sign-in; the first claimant only needs to know the registered email. Mailbox ownership is deliberately not verified, which is a documented event risk. All later sign-ins verify the password normally. Participant creation, team reassignment/removal, and password resets are admin-only and require step-up.

Idempotency: `POST /v1/submissions` requires an `Idempotency-Key` header (8..80 chars). Replay semantics are described in section 4.

## 1. Public (no auth)

| Method, path | Purpose | Response |
| --- | --- | --- |
| GET `/v1/public/event` | Public event card (PUB-01) | `{event: {slug, name, status, starts_at, ends_at, timezone, duration_minutes, max_participants, max_teams, rules_version, format}}`; `{event: null}` if nothing published |
| GET `/v1/public/rules` | Latest rules document | `{rules: {version, body_markdown, frozen_at, event_name}}` or `{rules: null}` |
| GET `/health` | Real probes, never fabricated green | 200 `{status:"ok", database_app:"up", database_gt:"up", clock}` or 503 `{status:"degraded", ...}`; a failed probe adds `database_*_error` with the exception class name |
| GET `/v1/time` | Server clock (unauthenticated) | `{server_time_ms, server_time}` from `clock_timestamp()` |

## 2. Auth and account

| Method, path | Role | Limits / notes | Body | Response, errors |
| --- | --- | --- | --- | --- |
| POST `/v1/auth/login` | anon | 20/min per IP + 10/min per email (in-process bucket); password min 12 chars | `{email, password}` | Only an email already registered by an admin can sign in; unknown email returns generic 401 `AUTH_INVALID_CREDENTIALS`. If the registered account has no `password_hash`, the password supplied on this first sign-in is saved as its initial password and the account becomes active. Otherwise the stored password is verified. This event intentionally does not verify mailbox ownership before allowing the first password claim; this risk is accepted for the event. Success returns `{access_token, expires_at, server_time, user{id,email,name,role,status}}` and sets the HttpOnly refresh cookie. AUTH-10 (max 2 live sessions) reclaims zombie sessions and evicts the oldest live session (audited `auth.session_evicted`). |
| POST `/v1/auth/refresh` | cookie | CSRF: `Origin` must equal an allow-listed origin AND header `X-BHCTF-Client` must be present (NET-05) | none | `{access_token, expires_at, server_time}`; rotates the refresh family. Reuse of a rotated token within 10 s grace returns the same successor (AUTH-04); reuse outside grace revokes the whole family and session. Errors: 403 `AUTH_FORBIDDEN` (bad Origin/header), 401 `AUTH_SESSION_REVOKED` |
| POST `/v1/auth/logout` | any | bearer required | none | `{ok:true}`; revokes all refresh tokens and the session, clears cookie |
| POST `/v1/auth/register` | admin only | Admin-created staff accounts; no password is set here | `{email, name, role: reviewer|organizer|admin}` | 201 `{ok, email, message, password_setup_required:true}`. The staff member sets their initial password on first sign-in. Participant registration is event/team-scoped at `POST /v1/admin/events/{id}/participants`.
| POST `/v1/auth/step-up` | any | 5 failures/300 s per user | `{password}` | `{ok, valid_until}`; records `sessions.step_up_at = now()`. Error: 401 `AUTH_INVALID_CREDENTIALS` |
| POST `/v1/auth/activate` | disabled | Not registered in the API for this event. No invite tokens or activation flow are used; admins register users and first sign-in sets an initial password when needed. | none | 404 `NOT_FOUND`.
| POST `/v1/auth/password/reset-request`, `POST /v1/auth/password/reset` | disabled | No self-service password reset routes are registered for this event. | none | 404 `NOT_FOUND`. Password reset is an admin-only action described in section 8.
| GET `/v1/me` | any | none | none | `{id, email, name, role, status, team{id,name,join_code,event_id,slug,event_status}|null, rules_accepted, rules_version}` |
| GET `/v1/me/sessions` | any | none | none | `[{id, created_at, last_seen_at, device(user_agent, 80 chars)}]` for unrevoked sessions |
| DELETE `/v1/me/sessions/{session_id}` | any | own sessions only | none | `{ok:true}`; revokes that session and its refresh tokens |
| POST `/v1/me/acceptances` | any | none | `{version?}` | `{ok, rules_version}`; inserts `acceptances` (unique per user+version) with hashed IP |

## 3. Round state and participant config

| Method, path | Role | Notes |
| --- | --- | --- |
| GET `/v1/state` | any authenticated | Latest stage of the latest event in ready/live/closed/finalized. Returns `{server_time, round{id,name,kind,status,event_status,starts_at,ends_at_effective,grace_seconds,paused}, rules_version, announcements_version, leaderboard_version, leaderboard_visibility}`. `ends_at_effective` includes summed `time_adjustments` for the caller (global + per-user). Participants poll this every 5 s |
| GET `/v1/config` | any authenticated | Safe operational config only (TIM-safe): `{stage_id, grace_seconds, runner_enabled, ai_assessment_enabled, scoring_weights, ctf_bonus_cap_points, limits{feedback_mode, leaderboard_mode, leaderboard_delay_s, max_span, max_reports_per_file, max_reports_per_round, file_cooldown_s, location_tolerance_lines, ctf_bonus_cap_points, penalty}, schedule}`. Never leaks ground-truth material (DAT-03) |

## 4. Challenges (participant delivery)

| Method, path | Role | Limits | Notes |
| --- | --- | --- | --- |
| GET `/v1/assignments` | any authenticated (content gated by team) | 30/min per user | Requires the current stage to be armed/active/paused/ended/evaluating/released/final. Returns `{assignments:[{challenge_id, slug, title, version, instructions_markdown, repo_url, codespace_url, stage_id, stage_status, files:[{path, lang, size_bytes, line_count}]}]}` or `{assignments:[], message}`. File list comes from `app.challenge_files`; the organizer answer key is never seeded into platform storage |
| GET `/v1/challenges/{challenge_id}/files/content?path=` | participant (+rules accepted) | 120/min per user | Path normalized with `os.path.normpath`; `..` prefixes, absolute paths and NUL bytes raise 400 `PATH_INVALID` (SEC-03). Unassigned/unknown challenge or file -> 404 `FILE_NOT_FOUND` (no existence oracle). Response `{path, lang, content, line_count, sha256}`, files > 512 KiB are refused |
| GET `/v1/challenges/{challenge_id}/search?q=` | participant (+rules accepted) | 30/min per user, `q` 2..100 chars | Server-side bounded `LIKE` search over `app.challenge_file_lines`, max 50 results, text truncated to 200 chars (WS-10). Same 404 discipline as above |

## 5. Submissions and drafts

### POST `/v1/submissions` (participant, rules accepted) -> 202

Rate limit 30/min per user. Required header `Idempotency-Key`. Body (`SubmissionIn`):

```json
{
  "kind": "bug_report | ctf_flag",
  "challenge_id": "optional, server ignores it and resolves the assignment itself (see tests/test_authorization.py)",
  "file_path": "routes/notes.js",       // bug_report: required, max 500 chars, must exist in the assigned challenge
  "line_start": 44, "line_end": 46,     // required, 1..100000, start <= end
  "related_locations": [{"file_path": "...", "line_start": 1, "line_end": 2}],  // max 5
  "category": "data_handling",          // must be in the 12-value taxonomy
  "claimed_severity": "low|medium|high|critical",   // informational only
  "title": "5..120 chars", "description": ">= 20 chars, <= 2000",
  "impact": "<= 500", "evidence": "<= 2000", "suggested_fix": "<= 500",
  "flag_code": "4..200 chars (ctf_flag only)"
}
```

Ordered transaction (routers/submissions.py):

1. Identity and team resolved from the token; team must be `active`.
2. Idempotency: existing `(author_id, idempotency_key)` row with the same canonical payload hash replays the original receipt (`"replay": true`) even after the deadline; a different payload raises 409 `IDEMPOTENCY_CONFLICT` (DAT-02).
3. Stage row locked `FOR UPDATE`, then `clock_timestamp()` read once (ADR-012).
4. State validation, first failure wins (spec 10.4): `paused` -> 409 `ROUND_PAUSED`; not active -> 409 `ROUND_NOT_ACTIVE` (ended/evaluating/released/final -> 409 `ROUND_ENDED`); `now > ends_at + adjustments + grace_seconds` -> 409 `ROUND_ENDED`.
5. Field validation; caps and cooldown read from stage config: `max_span` (default 25), `max_reports_per_file` (12, excluding `rejected_auto`), `max_reports_per_round` (80), `file_cooldown_s` (20) -> 429 `SUBMISSION_CAP_REACHED` / `SUBMISSION_COOLDOWN`, 404 `FILE_NOT_FOUND`, 422 `SUBMISSION_INVALID`.
6. Insert submission + `evaluate_submission` job + `refresh_leaderboard` job + outbox event + audit row in ONE transaction; receipt code is HMAC-derived (`security.receipt_code`).

Response: `{submission_id, receipt_code, received_at, status:"received", replay, team_id}`.

### Other endpoints

| Method, path | Role | Notes |
| --- | --- | --- |
| GET `/v1/submissions` | participant | Own last 200. Before release every live status collapses to `received` (INT-01); after `feedback_released` the row includes `status`, `matched_bug_code`, `points_visible`. `flag_code` is echoed only for ctf_flag rows |
| GET `/v1/submissions/{submission_id}` | participant | Ownership-scoped; another participant's id returns 404 `NOT_FOUND`, never 403 (SEC-02, IDOR). Full payload included; `matched_bug_code`/`points` only when released |
| POST `/v1/submissions/{submission_id}/dispute` | participant | 201 `{ok}`; own submission only, only after release (`feedback_released`), statement 10..1000 chars; sets status `disputed` |
| GET `/v1/drafts/{challenge_id}` | participant | `{payload, version, updated_at}` or empty defaults |
| PUT `/v1/drafts/{challenge_id}` | participant | Body `{payload, version?}`; version mismatch -> 412 `DRAFT_VERSION_CONFLICT`; returns the new version |

## 6. Leaderboard, results, announcements

| Method, path | Role | Notes |
| --- | --- | --- |
| GET `/v1/leaderboard/{stage_id}` | any authenticated | Stage config `leaderboard_mode` governs: `hidden` blocks participants until stage released/final; `frozen` serves only snapshots older than `leaderboard_delay_s` (default 600); `live` serves the latest snapshot. Returns `{visible:false,...}` or `{visible, version, snapshot, generated_at}`; `{visible:true, snapshot:null}` when nothing published yet |
| GET `/v1/results/{stage_id}` | participant | `{released:false,...}` until any stage submission has `feedback_released`. Then: team-scoped ledger breakdown `{released, total, by_component, penalties, entries[{component, kind, points, reason_code, title, receipt_code, matched_bug_code, received_at}]}` |
| GET `/v1/announcements` | any authenticated | `?limit<=100`, newest first: `[{id, severity, audience, body, author, created_at}]` |

## 7. Reviewer

| Method, path | Role | Notes |
| --- | --- | --- |
| GET `/v1/review/queue` | staff | Open/claimed items ordered by priority then age, limit 200. Item: `{review_item_id, submission_id, kind, title, file_path, line_range, category, received_at, priority, status, claimed_by_me}` |
| POST `/v1/review/items/{item_id}/claim` | staff | Row locked; refuses items claimed by another reviewer within the 10-minute claim timeout (`CLAIM_TIMEOUT_MIN`); sets submission to `in_review` |
| GET `/v1/review/items/{item_id}` | staff | `{review_item_id, status, submission{id, kind, title, payload, file_path, line_range, category, received_at, receipt_code, flag_code, status}, file{content, lang, line_count}|null, team_total_reports}`. Participant text is data, rendered as plain text by the SPA (EVA-05) |
| POST `/v1/review/items/{item_id}/decision` | staff | Body `{decision, rubric{functionality, root_cause, code_quality, testing, evidence (0..1)}, notes?<=2000}`. Decision values: `accept_full | accept_partial | duplicate | reject_invalid | unplanned_valid | needs_second_review`. Writes `app.reviews`, sets item `decided`, updates submission status, enqueues `apply_review_outcome`, writes audit with scope `review` |

## 8. Organizer / admin

Auth as listed; `step_up` column means the route additionally requires a fresh step-up (403 `ADMIN_STEP_UP_REQUIRED` otherwise).

| Method, path | Auth | Notes |
| --- | --- | --- |
| POST `/v1/admin/events` | organizer | Body `{name, slug, starts_at?, duration_minutes=180, max_participants=50, max_teams=12, timezone="Asia/Kolkata"}` -> 201 `{event_id}`. Config embeds the provisional-capacity note (ADR-011) |
| POST `/v1/admin/events/{id}/status` | organizer | `{status}`; validated transition, readiness gate before `live` (422 with pending count), sets event window on going live |
| POST `/v1/admin/events/{id}/config` | organizer | `{config, reason (5..500), freeze}`. Weights must sum to 1.0 (422 otherwise). Computes `rule_set_hash` = sha256 of canonical JSON; `freeze:true` snapshots the config onto the event (DAT-04) |
| GET `/v1/admin/events` | organizer | List with status, window, rules/config versions |
| POST `/v1/admin/events/{id}/stages` | organizer | Body `{name, kind="main", grace_seconds=3, config?}`; merges `DEFAULT_STAGE_CONFIG`, computes the stage `rule_set_hash` -> 201 `{stage_id, rule_set_hash}` |
| POST `/v1/admin/stages/{id}/control` | organizer | `{action: seal|arm|start|pause|resume|extend|end, reason, seconds?}`. Reason required (422). `extend` 1..3600 s. See state machine notes in SYSTEM_ARCHITECTURE.md Section 5.2 for the resume caveat |
| POST `/v1/admin/stages/{id}/release` | organizer | `{action: evaluate|release, reason}`; evaluate: ended->evaluating + `release_feedback` job; release: ->released + `release_feedback` + `refresh_leaderboard(public)` |
| POST `/v1/admin/events/{id}/teams` | organizer | `{name}` -> 201 `{team_id, join_code}`; enforces `max_teams` |
| GET `/v1/admin/events/{id}/teams` | organizer | Event-scoped team list with member counts; join codes are not returned |
| POST `/v1/admin/events/{id}/participants` | admin + step_up | `{email, name, team_id}` -> 201 `{participant_id, email, team_id, password_setup_required}`. Creates/reuses a passwordless participant account and assigns it directly to a team. No invite or activation token is created. |
| GET `/v1/admin/events/{id}/participants` | organizer | Registered participant roster, including `password_set`; no invite tokens or password hashes returned |
| PATCH `/v1/admin/events/{id}/participants/{user_id}/team` | admin + step_up | `{team_id}`; moves the participant to another team within the same event |
| DELETE `/v1/admin/events/{id}/participants/{user_id}` | admin + step_up | Removes the participant's event membership. If no other team membership remains, disables the account and revokes its sessions |
| POST `/v1/admin/users/{user_id}/password-reset` | admin + step_up | `{password (min 12)}`; sets a new password, increments `token_version`, revokes all active sessions and refresh tokens. An administrator cannot reset their own password through this route |
| POST `/v1/admin/events/{id}/challenges` | organizer + step_up | `{slug, title, instructions_markdown?}` -> 201 `{challenge_id}` |
| POST `/v1/admin/challenges/{id}/files` | organizer + step_up | multipart `file` (path = filename); 512 KiB max; traversal rejected (`PATH_INVALID`); upserts file content + rebuilds the line index (first 5000 lines, 500 chars each); refused once status is sealed/published (409 `GROUND_TRUTH_LOCKED`) |
| POST `/v1/admin/challenges/{id}/freeze` | organizer + step_up | Requires >= 1 file; computes `content_hash` over ordered file hashes; sets status sealed (409 `GROUND_TRUTH_LOCKED` otherwise) |
| POST `/v1/admin/challenges/{id}/publish` | organizer | `{stage_id}`; sealed only; sets published and creates the `assignments` row |
| POST `/v1/admin/challenges/{id}/ground-truth/bugs` | organizer + step_up | `{code<=40, title, severity, category, points>0<=1000, root_cause_group, explanation, fix_guidance?, locations<=8, aliases<=12}` -> 201 `{bug_id}`. Writes via `gt_session` only; refused after seal (DAT-03/04) |
| GET `/v1/admin/challenges/{id}/ground-truth/flags` | organizer + step_up | Lists `{id, flag_key, points, max_awards, position_label, created_at}`. Never returns plaintext flag codes or stored hashes |
| POST `/v1/admin/challenges/{id}/ground-truth/flags` | organizer + step_up | `{flag_key, code 4..200, points>0<=500, max_awards>=1}` -> 201 `{flag_id, ok}`; stores only the HMAC `code_hash`, plaintext never persisted; flag key and code must be unique within the challenge |
| PATCH `/v1/admin/challenges/{id}/ground-truth/flags/{flag_id}` | organizer + step_up | Partial body `{flag_key?, code?, points?, max_awards?}`; edits a flag on a draft/reviewed challenge only. Omit `code` to retain the existing secret; list/read responses never expose the code or hash |
| POST `/v1/admin/challenges/{id}/ground-truth/test-results` | organizer + step_up | `{submission_id, tests_passed, tests_total}`; enqueues `apply_test_results` (ADR-019); audit scope `scoring` |
| POST `/v1/admin/events/{id}/announcements` | organizer | `{severity: info|warning|critical, body 3..1000}` |
| POST `/v1/admin/events/{id}/time-adjustment` | organizer | `{stage_id, delta_seconds (nonzero), reason_code: ACCOMMODATION|OUTAGE|DEVICE_FAILURE|OTHER, user_id?}`; per-user when `user_id` set, global otherwise (TIM-05) |
| GET `/v1/admin/events/{id}/live` | organizer | Control room: submission status counts, team/participant counts, job queue `{oldest_queued_age_s, dead_letters}`, review backlog, stages, health probes, kill-switch state. `participants_online_estimate` is accounts-with-membership, not live presence |
| GET `/v1/admin/events/{id}/readiness` | organizer | Checklist rows |
| POST `/v1/admin/events/{id}/readiness/{key}` | organizer | `{state: checked|unchecked|na}`; gates the live transition |
| GET `/v1/admin/audit/verify` | organizer | Recomputes the hash chain per scope: `{scope, entries, verified, broken_at|null, intact}` |
| GET `/v1/admin/audit` | organizer | `?limit<=500&action=`; newest first, no actor_id in the listing projection |
| GET `/v1/admin/events/{id}/export/{kind}` | organizer | `kind = results|submissions|ledger`; returns `{filename, content}` CSV text, computed on demand |
| GET `/v1/admin/disputes` | organizer | All disputes with submission title/receipt |
| POST `/v1/admin/disputes/{id}/resolve` | organizer | `{outcome: upheld|rejected, resolution, adjust_points?}`; upheld with `adjust_points` appends a `DISPUTE_UPHELD` ledger adjustment (never mutates old entries, DAT-05) |
| POST `/v1/admin/adjustments` | admin only | `{stage_id, team_id, points (nonzero), reason}` -> `MANUAL` ledger adjustment, audit scope `scoring` |
| GET `/v1/admin/system/health` | organizer | DB probes, job counts by status, oldest queued age, profile, kill switches |

## 9. Error codes (catalog)

`AUTH_INVALID_CREDENTIALS` 401, `AUTH_TOKEN_EXPIRED` 401, `AUTH_SESSION_REVOKED` 401, `AUTH_RATE_LIMITED` 429, `AUTH_FORBIDDEN` 403, `AUTH_NOT_ACTIVATED` 403, `RULES_NOT_ACCEPTED` 403, `ROUND_NOT_ACTIVE` 409, `ROUND_PAUSED` 409, `ROUND_ENDED` 409, `SUBMISSION_INVALID` 422, `SUBMISSION_CAP_REACHED` 429, `SUBMISSION_COOLDOWN` 429, `IDEMPOTENCY_CONFLICT` 409, `DRAFT_VERSION_CONFLICT` 412, `RUN_RATE_LIMITED` 429, `RUN_LANGUAGE_UNSUPPORTED` 422, `RUNNER_UNAVAILABLE` 503, `FILE_NOT_FOUND` 404, `PATH_INVALID` 400, `KEY_NOT_AVAILABLE` 425, `SERVICE_DEGRADED` 503, `ADMIN_STEP_UP_REQUIRED` 403, `GROUND_TRUTH_LOCKED` 409, `FLAG_INVALID` 422, `FLAG_ALREADY_SUBMITTED` 409, `NOT_FOUND` 404, `VALIDATION_ERROR` 422, `INTERNAL_ERROR` 500. The `RUN_*`, `KEY_NOT_AVAILABLE`, `FLAG_*` and `SERVICE_DEGRADED` codes are defined for spec compatibility but are not raised anywhere in the current code (verified by grep); the runner and flag endpoints they were designed for are either disabled (ADR-010) or implemented through the generic submission path with `SUBMISSION_INVALID`/`duplicate` outcomes instead.

Rate limiting is an in-process sliding-window bucket keyed by identity (`deps.rate_limit`, NET-01 identity keys, SEC-07 caveat: per-replica, resets on restart). `max_concurrent_requests_per_user` exists in `config.py` but no middleware enforces it yet.
