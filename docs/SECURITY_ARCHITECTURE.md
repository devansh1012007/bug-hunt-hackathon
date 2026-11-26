Status: describes the implementation as of 2026-11-26

# Security Architecture

The audience of this event finds bugs for fun (SEC-01): every participant is treated as a capable adversary of the platform itself. This document describes what is actually implemented in `backend/app/` and `frontend/src/`, what verifies it, and what is honestly NOT covered yet.

## 1. Authentication design

- Passwords: Argon2id via `argon2-cffi`, OWASP-minimum parameters `time_cost=2, memory_cost=19456 KiB, parallelism=1` (`security.py`, AUTH-07). Minimum length 12 enforced on first sign-in and admin password reset. Accounts are created only by admins. If a registered account has no password hash, the password supplied on first sign-in is stored as its initial password; otherwise it is verified normally. Unknown emails receive generic `AUTH_INVALID_CREDENTIALS` (ATH-01, no account enumeration).
- Access tokens: PyJWT HS256 with the algorithm pinned to `settings.jwt_algorithm` in `decode_access_token`, so `alg` confusion and `none` are rejected (AUTH-03). Claims: `iss=bughunt-api` (verified), `aud=bughunt-participant|bughunt-admin` (verified, tried in that fixed order), `sub`, `sid` (session id), `role`, `tv` (token_version), `iat`, `nbf`, `exp`, `jti`. TTL 600 s. HS256 is acceptable for the single-service D1 profile; EdDSA with a key file is the documented production upgrade path (`config.py` comment).
- Token lifecycle vs. server state: every authenticated request re-reads `app.users` + `app.sessions` (`deps.get_principal`), so an admin password reset (`token_version` bump), logout, session revocation and disqualification revoke access tokens instantly without a denylist (AUTH-01). Public activation and self-service reset endpoints are not registered; the legacy invite and password-reset token tables are dropped by migration 0004.
- Token storage: the SPA keeps the access token in a module variable (memory only, AUTH-02); no localStorage. The refresh token never touches JavaScript: HttpOnly cookie `bhctf_refresh`, `SameSite=Strict`, `path=/v1/auth/refresh` (scoped so it is not sent with any other request), `Secure` when `ENVIRONMENT=production`, `max_age` = refresh TTL (3 days default).
- Sessions: max 2 live sessions per user (AUTH-10). At login the server takes a per-user row lock (concurrent logins cannot exceed the cap), reclaims zombie sessions whose refresh tokens are all expired/revoked, and if still at the cap evicts the oldest live session (session revoked, token family revoked, `auth.session_evicted` audit entry). Sign-in is never blocked by stale sessions. Session rows carry hashed IPs and a truncated user agent; `GET /v1/me/sessions` lets a user review and revoke their own devices.
- Revocation: an admin password reset bumps `users.token_version` and revokes all sessions + refresh families (ATH-03); disqualified users get 403 on every request.

## 2. Refresh rotation and reuse detection (AUTH-04)

`POST /v1/auth/refresh`:

1. CSRF first: `Origin` must exactly match an allow-listed origin AND the custom `X-BHCTF-Client` header must be present (NET-05; the cookie alone can never trigger a rotation from another site).
2. The presented token is SHA-256 hashed and looked up. `revoked` or expired -> 401.
3. `rotated` token: if reused within `refresh_reuse_grace_s` (10 s), the encrypted successor is decrypted (Fernet, key derived from `JWT_SECRET`) and returned, so parallel tabs racing the same refresh both succeed with the SAME successor. Outside the grace window the whole family and the session are revoked (theft response).
4. Normal rotation: a new token joins the family (`replaces_id`), the old one becomes `rotated` with the successor encrypted at rest, a fresh access token is issued.

Verified by `tests/test_auth.py::test_refresh_rotation_and_reuse_detection`.

## 3. CSRF and CORS

- CORS: exact-origin allow-list from `frontend_origin` (comma-separated), `allow_credentials=True`, explicit method and header lists, no wildcards (NET-05). In dev the SPA is same-origin through the Vite proxy, so cookies are always first-party.
- CSRF: the only cookie-authenticated endpoint is `/v1/auth/refresh`; it enforces the Origin check plus the custom header (`deps.check_csrf`). Bearer-authenticated endpoints are not cookie-form-able, and the custom header requirement also blocks simple-form cross-site calls.
- State-changing endpoints require the `Authorization` header, which a cross-site attacker cannot supply.

## 4. Rate limits (identity-based, NET-01/SEC-07)

In-process sliding window (`deps.rate_limit`): login 20/min per IP and 10/min per email; step-up failures 5 per 300 s per user; submissions 30/min per user; assignments 30/min; file content 120/min; search 30/min; reset-request 3/600 s per IP. Keys are identity or identity+action wherever authenticated, because the venue shares one public IP (NET-01). Honest limits (SEC-07): the bucket is per-process, resets on restart, and does not coordinate across replicas; a single-replica D1 deployment makes this correct, and a Redis-backed shared limiter is the documented multi-replica fix. `max_concurrent_requests_per_user` in `config.py` is declared but not enforced by any middleware yet. The authoritative anti-abuse controls for scoring are the database-side caps (Section 6), not these buckets.

## 5. Step-up re-authentication (AUTH-09)

Sensitive organizer routes (challenge create/upload/freeze, ground-truth bugs/flags/test-results) depend on `deps.step_up_verified`: the session's `step_up_at` must be within the last 5 minutes, else 403 `ADMIN_STEP_UP_REQUIRED`. `POST /v1/auth/step-up` verifies the password and stamps the session. The SPA surfaces the gate (`StepUp.tsx`) and retries after the organizer confirms. This is password re-entry, NOT MFA: WebAuthn or Cloudflare Access in front of the admin surface is the documented production hardening (ADR-008), and is listed as not covered in Section 10.

## 6. Input handling and the verdict-oracle defense

- Body size: 413 above 512 KiB at the middleware (`content-length` check).
- Validation: Pydantic models with length/pattern bounds on every field; report category must be in the 12-value taxonomy; the server resolves the challenge from the assignment, so a forged `challenge_id` in the body is ignored (verified in `tests/test_authorization.py::test_forged_challenge_id_is_ignored_by_server`).
- Path traversal (SEC-03): file lookups normalize the path with `os.path.normpath` and reject `..` prefixes, absolute paths and NUL bytes with 400 `PATH_INVALID`; content is served only from `app.challenge_files` rows (never the filesystem), so even a missed normalization cannot escape the database. Verified by e2e and unit tests.
- Stored content is inert: uploaded challenge code is text in rows, never executed on platform infrastructure.
- Verdict oracle (INT-01, the highest-value integrity control): `feedback_mode=hidden` coarsens every participant-visible status to `received` until `release_feedback` sets `feedback_released`; verdicts, matched bug codes and points appear only at release. Combined with caps (max_span 25, 12/file, 80/round), the 20 s per-file cooldown, and the release-only capped penalty, binary-searching the answer key through feedback is not possible. A live leaderboard mode exists in config and reveals only snapshot ranks, never per-report verdicts.
- Penalties are computed once at release from definitively `rejected_auto` reports (fp_factor 0.25 of the lowest severity, capped at 20% of stage max, INT-11); plausible-but-unmatched reports go to review, never auto-penalized (INT-02).

## 7. IDOR and object access (SEC-02)

- Every participant query filters by `author_id` or `team_id` taken from the token, then again in service queries. `GET /v1/submissions/{id}` returns 404 (not 403) for foreign ids so existence is not disclosed; verified by `test_authorization.py` and the e2e IDOR check.
- Resource ids are UUIDv7, not sequential (`db.uuid7`); sequential ids are never an access control.
- Reviewer endpoints are staff-gated; reviewer decisions claim-lock items to one reviewer for 10 minutes.
- Challenge content requires an assignment row for the caller's team, and 404s are indistinguishable for unknown vs. unassigned challenges.

## 8. Ground-truth isolation (DAT-03/04)

Full detail in `GROUND_TRUTH_ISOLATION.md`. Summary: the answer key lives in schema `gt`, reachable only by the `gt_admin` role; the HTTP application connects exclusively as `app_api`, which PostgreSQL blocks from gt reads and writes; gt writes require step-up and are refused once the challenge version is sealed; flag plaintexts are stored only as HMACs; the organizer answer key file is never ingested into platform storage and the e2e suite asserts `ORGANIZER_ANSWER_KEY.md` is not served.

## 9. Audit chain and logging

- Every state-changing action writes an `audit.entries` row in the SAME transaction as the change (`audit.audit_write`), chained per `event_scope` with SHA-256 (`prev_hash` -> `hash`). `GET /v1/admin/audit/verify` recomputes the chain and reports the first break. Scopes in use: `global` (default), `event:{id}`, `challenge:{id}`, `challenge`, `review`, `scoring`.
- Honest limitation (architecture 9.5): the chain is tamper-EVIDENT against partial tampering, not tamper-PROOF against an attacker who controls the database; D1 mitigation is exporting signed checkpoints to a separate account, which is not implemented yet (see BACKUP_AND_RECOVERY.md).
- Logs: request id, route, level; passwords and tokens are not logged. The one deliberate exception is the dev-only `[dev-mail]` password-reset line that prints the raw reset token to the server log because there is no email provider (documented in the runbook; acceptable only because dev logs stay on the operator's machine).

## 10. Threat model: top risks and mitigations

| Risk (register id) | Scenario | Implemented mitigation | Residual gap |
| --- | --- | --- | --- |
| Verdict oracle (INT-01) | Probe line ranges to read the answer key from feedback | Hidden feedback until release, caps, cooldown, release-only capped penalty | Reviewer notes could leak hints after release; review the release payload before publishing |
| IDOR (SEC-02) | Read another team's report | Token-scoped queries, 404 discipline, UUID ids, automated tests | Double-review flow not wired; reviewer queue shows all teams by design |
| Replay (DAT-02) | Duplicated submissions from retries | `UNIQUE (author_id, idempotency_key)` + canonical payload hash; same-payload replay returns the original receipt even after deadline | None known; covered by tests |
| Account discovery / first claim (ATH-01) | Discover valid emails or claim an account before its intended user | Unknown login emails receive the same generic credential error; registration is admin-only; no invite or self-service reset tokens are issued and migration 0004 drops their legacy tables | Accepted event-specific risk: the first person who knows a registered email can claim its initial password because mailbox ownership is not verified. Admins must distribute registered emails through a controlled channel |
| Admin takeover (AUTH-09) | Stolen organizer session | Step-up within 5 min for gt routes, 2-session cap, instant revocation via token_version | No MFA yet; Cloudflare Access + WebAuthn planned for D1 |
| Upload abuse (SEC-03) | Malicious challenge upload | Organizer-only + step-up, 512 KiB cap, path normalization, inert text storage | Multi-file zip ingestion not implemented (one file per request) |
| Report text attacks reviewer (EVA-05) | XSS/HTML in report payload | Participant content stored as JSONB, rendered as plain text by `PlainMarkdown`; no `dangerouslySetInnerHTML` in `frontend/src`; `X-Content-Type-Options: nosniff` on all responses | No strict CSP header yet on the SPA host (add at the edge in D1) |
| WAF vs. attack-like reports (NET-02) | Managed rules 403 SQLi-looking reports | Not deployed yet; edge configuration is a D1 runbook item with a scoped skip for submission paths | Pending D1 edge setup |

## 11. What is NOT covered yet (honest list)

1. MFA for organizer/admin accounts (step-up is password re-entry). Production path documented: Cloudflare Access + WebAuthn (ADR-008).
2. External WAF / rate limiting at the edge, and Cloudflare-specific defenses (NET-02/03/05): the D1 edge is not set up in this repo at all.
3. An external penetration test: no pentest report exists; the readiness checklist item `pen_test` ("Security review completed; high/critical closed") is UNCHECKED in the seed data by design and gates going live.
4. Strict CSP, HSTS and additional security headers: the backend sets nosniff, Referrer-Policy and Cache-Control only; CSP/HSTS belong on the Pages/proxy layer in D1.
5. Audit checkpoint export to an external account (tamper evidence beyond the live database).
6. Cross-replica rate limiting (SEC-07) and enforcement of the declared per-user concurrency cap.
7. Supply-chain scanning (SEC-04): dependencies are pinned in `requirements.txt` / lockfiles, but no automated vulnerability scanning is configured.

## 12. Secrets handling

- `JWT_SECRET` signs access tokens, derives the Fernet key for successor encryption, and keys the HMAC receipt/flag/ip hashes. It defaults to a dev-only string that `config.py` marks as never-for-production; production must inject it via environment. `tests/conftest.py` sets a test-specific secret.
- Database credentials (`app_api`, `gt_admin`) are dev constants in `scripts/dev_db.sh`; D1 must replace them and keep the two roles distinct. No secrets are embedded in the frontend bundle (verified: the SPA only talks same-origin `/v1`).
- Flag plaintexts exist in exactly two places: `backend/seed.py` (dev seed, HMACs only are persisted) and the organizer-held `challenge-app/ORGANIZER_ANSWER_KEY.md` (never ingested by the platform).
