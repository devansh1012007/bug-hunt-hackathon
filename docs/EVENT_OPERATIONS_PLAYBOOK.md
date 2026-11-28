Status: describes the implementation as of 2026-11-22

# Event Operations Playbook (Fall Back, 6 December 2026)

Event-day operations manual for the organizers of Fall Back, the full-stack
debugging challenge run by the Software Development Club, VIT Bhopal
University. Every control named here exists in the platform today; the admin
actions are the real endpoints behind the Operations console
(`/admin`) described in `docs/PRODUCT_SPEC_WEBSECTIONS.md`. Open fields are
marked in place.

## 0. Roles on the day

- Event lead: owns the run of show and the schedule phases.
- Platform admin: owns the Operations console, stage control, and the worker
  process. In the seed data the admin and organizer accounts are
  admin@example.com and organizer@example.com with organizer2@example.com;
  replace these with real staff accounts before the event.
- Review lead: owns the reviewer queue and the ground-truth audit trail.
- Floor proctors: walk the hall, handle device and account issues, escalate.

Contact channel for the desk: [TO BE FILLED: control room phone or radio
channel].

## 1. T-minus checklist mapped to platform readiness items

The event row carries eight readiness items (seeded by `backend/seed.py`,
editable at Operations > Readiness gate). The event cannot move from ready to
live while any item is in the unchecked state (ADM-EV-05 gate in
`backend/app/routers/admin.py`); marking an item N/A clears it from the gate,
so use N/A only for items that genuinely do not apply. Check each item only
when its evidence exists; the check action records who and when through the
audit log.

| When | Readiness key | What must be true | Owner |
| --- | --- | --- | --- |
| T-14 days | load_test_50 | Load test at the approved cap passes thresholds. Provisional caps are 50 participants and 12 teams; 12 teams of 5 exceeds the participant cap, so confirm the final numbers before publishing (ADR-011). | infrastructure lead |
| T-14 days | gt_isolation | The app_api database role cannot read the gt schema, tested with the real role, not with gt_admin. | application lead |
| T-10 days | backup_restore | Backup and restore rehearsal completed and timed; you know the restore time you can promise. | infrastructure lead |
| T-7 days | pen_test | Security review completed; high and critical findings closed. | application lead |
| T-5 days | challenge_audit | Two independent reviewers certified the challenge (INT-02 protocol in `docs/CHALLENGE_DESIGN_AND_GROUND_TRUTH.md`). | review lead |
| T-3 days | dry_run | Full dry run with real users on the venue network, including stage seal, arm, start, pause, resume, end, evaluate, release. | event lead |
| T-2 days | rules_published | Rules, privacy notice, and consent flows published; the rules document frozen at `POST /v1/public/rules`. | organizer |
| T-1 day | runbooks | This playbook rehearsed; roles assigned; printer copies at the desk. | event lead |

T-0 morning additions (not platform gated, do them anyway): confirm the worker
process is running (`python worker.py`), confirm `/health` returns status ok
with both databases up, confirm the venue LAN serves the platform and blocks
the AI services per the participant AI policy, and print the join codes.

## 2. Reason codes

Stage control requires a reason string on every action and the API rejects
empty ones. Use the convention the Operations console placeholder teaches:
a short code, a space, then a few words, for example `TEC-104 projector
outage`. Time adjustments additionally require one of the four API codes:
ACCOMMODATION, OUTAGE, DEVICE_FAILURE, OTHER. Map the incident families like
this:

| Code family | Used for | API reason_code |
| --- | --- | --- |
| TIM-10 venue power | Building or hall power loss | OUTAGE |
| TIM-11 platform outage | Platform unreachable from the hall | OUTAGE |
| TIM-12 AV failure | Projector or sound failure during briefing | OTHER |
| TIM-13 device failure | One participant's laptop dies | DEVICE_FAILURE (per-user adjustment) |
| TIM-14 accommodation | Accessibility adjustment requested via profile | ACCOMMODATION |
| TIM-15 network degradation | LAN or uplink slow but up | OUTAGE |
| TIM-99 other | Anything that does not fit | OTHER |

Keep codes stable across the day; the audit trail and the dispute window will
thank you.

## 3. Pre-launch configuration sequence (T-60 to T-15 minutes)

All actions below run in the Operations console as organizer or admin. Every
one of them writes an audit entry.

1. Freeze the scoring and stage configuration. `POST
   /v1/admin/events/{event_id}/config` with the final config and `freeze:
   true` and a reason such as `CONFIG-FREEZE final scoring before doors`. The
   API validates that scoring weights sum to 1.0 (DAT-04). After freeze, the
   rule set hash returned by stage creation and seeded into the challenge is
   what evaluations were matched against; do not change stage caps after
   reports start arriving.
2. Confirm the challenge version is sealed and published. Ground-truth and
   content operations require step-up (`POST /v1/auth/step-up`, valid 5
   minutes). The version must be sealed (frozen with a content hash) before
   `POST /v1/admin/challenges/{id}/publish` attaches it to the stage. In the
   seeded event this is already done by `backend/seed.py`.
3. Confirm the stage exists with the frozen rule set. Stages are created with
   `POST /v1/admin/events/{event_id}/stages`; the seeded stage is "Fall Back
   Main Challenge" with feedback_mode hidden, leaderboard_mode hidden, max
   span 25, 12 reports per file, 80 per round, 20 second cooldown, location
   tolerance 2 lines, CTF cap 100, penalty fp_factor 0.25 and cap_fraction
   0.2 applied at release only.
4. Walk the readiness gate. Check the eight items with evidence; leave
   unchecked anything you cannot evidence. The gate is honest: it blocks go
   live and names the number of unchecked items.
5. Verify audit chain health: Operations > Audit > Verify chain must report
   "Hash chain intact". A broken chain before doors is a stop-and-investigate
   condition.
6. Confirm account provisioning in Admin > Participants. An administrator
   registers each participant with an email, name and team assignment. There
   are no invite tokens, activation URLs or public activation endpoint. On
   first sign-in, only a registered participant who already belongs to a team
   may choose an initial password. Staff accounts are created through
   `POST /v1/auth/register` with an administrator-assigned password and
   step-up confirmation. Unknown emails, incorrect passwords, passwordless
   staff and participants without team membership receive the same generic
   invalid-credentials response.

## 4. Launch sequence (doors to first submission)

Exact order, with the console action and the endpoint behind it:

1. `configured -> ready`: Event transitions card, "To ready". Endpoint `POST
   /v1/admin/events/{event_id}/status` with `{"status": "ready"}`. Reason:
   n/a for event transitions (the reason field is required on stage control,
   not on event status).
2. Readiness gate passes. If the go-live call is rejected, the error names the
   unchecked count; fix or N/A the items honestly and retry.
3. `ready -> live`: "To live". Endpoint as above with `{"status": "live"}`.
   The server stamps starts_at (if empty) and ends_at = now + duration (180
   minutes). This is the go/no-go commit.
4. Stage `seal`: Stage control, action seal, reason required, for example
   `SEQ-01 sealing main stage for launch`. Endpoint `POST
   /v1/admin/stages/{stage_id}/control` with `{"action": "seal", "reason":
   "..."}`. Draft to sealed.
5. Stage `arm`: same endpoint with `{"action": "arm"}`. Sealed to armed.
   Arming releases challenge materials to the lobby and workspace; the
   countdown to start appears for participants.
6. Stage `start`: same endpoint with `{"action": "start"}`. Armed to active.
   The server stamps started_at and ends_at = now + 180 minutes from the
   database clock (ADR-012). Submissions are now accepted; the briefing phase
   is over when you press this.
7. Post the opening announcement: Announcement composer, severity info, body
   such as the round start time and the desk location. Critical severity
   exists for genuine emergencies; do not dilute it.

Alignment with the proposal schedule: the seeded phases (registration and
setup 15, briefing 10, part 1 30, part 2 35, part 3 60, part 4 25, final
evaluation 5) sum to exactly the proposal's 180 minutes, and stage start sets
a 180 minute server clock, so the phases map inside the round window. The
operational choice to make in the dry run is when to press start and when to
press end: if you press start at the official start time, the platform clock
covers the whole run of show including the briefing; if you run registration
and briefing before the window, the clock covers parts 1 to 4 plus the
evaluation phase with margin, and you can end the stage when part 4 closes to
enforce the evaluation phase as a no-submissions window. Pick one alignment
in the dry run and keep it on the day. The platform preset mirrors the
proposal schedule; do not change phases on the day.

## 5. Control room loop (every 5 minutes during the round)

Open Operations > Live operations. The panel auto-refreshes every 5 seconds;
walk this list top to bottom:

- Submission counts by status. Healthy mid-round: received grows, a minority
  sits in needs_review, matched_auto and rejected_auto appear as evaluation
  keeps up. A received count that grows without any evaluation status moving
  for minutes means the worker is behind or down (see incident 1).
- Job queue. Oldest queued job age above about 30 seconds sustained is a
  backlog warning; dead letters above zero always deserve a look (open System
  health for job counts by status and the error state).
- Review backlog. Open review items above 10 turns the tile warn-toned.
  Route reviewers to the queue; borderline matches arrive with priority 5 and
  auto-accepted confirmations with priority 7, so a flood of 5s means the
  matcher is finding near misses worth human eyes.
- Databases. Both app and gt probes must read up. The participant API needs
  the app database; evaluation needs the gt database; /health reports 503
  degraded when the app database is down.
- Participants estimate. This is the count of accounts in teams, not live
  presence; do not read it as "online now". Real attendance is a proctor
  count.
- Kill switches. Runner, AI assessment, and telemetry should read off all
  round; these come from deployment settings, not toggles.
- Announcements. Use info for logistics, warning for issues with workaround
  (for example "search is rate limited, wait a few seconds"), critical only
  for stop-level events (evacuation, platform-wide outage). Every
  announcement reaches every signed-in console within the 30 second poll.

Keep the receipts discipline: when a team shows a receipt code at the desk,
verify it against Submissions in the exports or the admin views rather than
guessing; receipt codes are HMAC-derived from the submission id and received
time, so the desk can confirm a filing happened at a claimed time.

## 6. Pause, resume, extend

All via Stage control with a reason code. Effects are server side and
race-safe: submissions checked against round state at commit time
(`ROUND_PAUSED`, `ROUND_ENDED`), and the receipt replay rule means a
committed report stays valid even if the clock moved.

- Pause (`{"action": "pause"}`): active to paused. Submissions reject with
  ROUND_PAUSED; participant countdowns show "Timer stopped". Use for venue
  emergencies only; it stops the clock but costs momentum.
- Resume (`{"action": "resume"}`): paused to active. The design intent is to
  shift ends_at by the pause duration so teams keep the promised working time
  (TIM-05). Implementation caution: the resume SQL in the current build
  compensates from started_at rather than the pause window and its comment
  says the precise approach is pending. After any resume, compare the stage
  ends_at shown in Stage control against what you intend, and correct with an
  extend or a time adjustment if the numbers are off. Rehearse this in the
  dry run (readiness item dry_run).
- Extend (`{"action": "extend", "seconds": N}`, N in 1..3600): shifts ends_at
  forward. Use for short recoveries; announce immediately (severity warning)
  with the new end time in IST.
- Per-user time adjustments: `POST /v1/admin/events/{event_id}/time-adjustment`
  with stage_id, delta_seconds, reason_code (ACCOMMODATION, OUTAGE,
  DEVICE_FAILURE, OTHER), and optionally user_id. Use for accessibility
  accommodations and single-team device failures. The effective end time in
  `/v1/state` includes these, so the affected participant's countdown is
  correct without a global change.
- End (`{"action": "end"}`): active or paused to ended. Submissions after end
  reject with ROUND_ENDED (committed-key replays still return their original
  receipt). The ConfirmButton arms twice on purpose.

## 7. Common incidents and first responses

### 7.1 Worker down

Symptoms: received submissions never move to matched_auto, needs_review, or
rejected_auto; oldest queued job age climbs; System health shows a growing
queued count.
Response: jobs are rows in the app database; nothing is lost. Restart the
worker (`python worker.py`; it recovers expired leases and resumes). At-least-once
delivery plus idempotent handlers (award claim uniqueness, component checks)
means a restart cannot double-score. If the worker cannot start, keep the
round running: submissions still land with receipts; evaluation catches up.

### 7.2 App database down

Symptoms: /health returns 503 with status degraded; participants see the
offline banner; logins fail.
Response: this is a stop-level event. Announce critical with the nature of
the outage, pause the stage (TIM-11), restore from the rehearsed backup if
needed, then resume and compensate time with a global adjustment (OUTAGE).
Receipts issued before the outage remain valid.

### 7.3 Ground truth (gt) database down

Symptoms: evaluation jobs fail and retry with backoff (10, 60, 300, 900
seconds) and eventually dead-letter; participants keep submitting fine.
Response: no participant impact while it is brief. Restore gt access, then
requeue dead evaluation jobs after confirming the handler state. The matcher
is a pure function of report, ground truth revision, rule set, and claims, so
replays are safe.

### 7.4 CDN or tunnel down (deployment exposure lost)

Symptoms: the platform is healthy on the VM (health ok from localhost) but
unreachable from the hall.
Response: switch the hall to the fallback path rehearsed in the dry run
(direct LAN route to the VM). Announce warning with instructions. If time was
lost, compensate with TIM-11 adjustments. Escalate to a critical announcement
and pause only if the fallback also fails.

### 7.5 Cheating or scope-violation report

Response order: preserve, then decide. 1) Note the time and the reporting
team; the audit log and request ids are your friends. 2) Preserve evidence:
exports (submissions, ledger) and the audit chain verify result. 3) Do not
announce anything naming a team. 4) The organizers decide per the published
rules. An administrator removes the participant from the event team through
Admin > Participants and records the reason in the audit trail. Account
lifecycle status is not used for disqualification; user rows remain for
ledger/audit foreign keys. Removing the final team membership revokes the
participant's sessions and prevents future sign-in. Do not edit user rows or
score history directly.

### 7.6 Report cap or cooldown complaints

These are policy, not incidents. The caps are server side (12 per file, 80
per round, 20 second cooldown) and visible to participants in `/v1/config`;
explain rather than override. Real evaluation interference goes through the
review queue, not cap changes.

### 7.7 First sign-in or forgotten password

There is no invite-reissue or public activation flow. A registered participant
with an existing team membership and no password sets the initial password
during sign-in. Staff accounts never use passwordless first-claim; their
initial password is assigned by an administrator.

For a forgotten password, an administrator uses Admin > Participants or the
admin password-reset operation after step-up confirmation. The administrator
assigns a new password; the reset revokes the user's sessions and increments
the token version. Deliver the new password through an organization-approved
authenticated one-to-one channel, never by email, event chat or team chat.
Unknown emails, passwordless staff and unassigned participants are not
repaired by creating public tokens; verify registration and team membership
through the admin roster.

## 8. Finalize sequence (end to release)

1. Stage `end` at the deadline (or after the last extension). Confirm the
   ended status chip.
2. Evaluate: Release row, Evaluate button. `POST /v1/admin/stages/{stage_id}/release`
   with `{"action": "evaluate"}`. This moves the stage to evaluating. Each
   submission's evaluation job was enqueued when that submission was accepted;
   the worker continues draining those jobs. This action does not apply
   release-default review points or penalties.
3. Record functional test results while ended/evaluating: Challenge tools >
   Ground-truth test results (step-up required), one submission at a time
   (`submission_id`, tests_passed, tests_total). Each recording enqueues the
   functional_test ledger application (ADR-019). It must be accepted before
   result publication.
4. Clear required review work. Submissions in needs_review/in_review and
   open review items other than optional auto-accept confirmation must be
   resolved. Queued, running or dead-lettered scoring jobs also block release.
   Recover or replay failed jobs; do not treat a dead-letter as completed.
5. Release: Release row, Release results button, `{"action": "release"}`.
   The API refuses publication while scoring jobs or required reviews remain.
   The stage enters released, but participant data stays hidden until the
   release worker applies default review quality (0.8, RELEASE_DEFAULT,
   disputable) where needed, applies capped penalties, sets
   `feedback_released` and `results_published_at`, and queues a fresh public
   leaderboard snapshot in one transaction. Pre-publication snapshots are
   not served as final results. Participants see scores only after the
   publication marker commits and a post-publication snapshot is available.
   If the release worker fails, repair/replay the job or retry Release after
   blockers clear; do not manually set the publication marker.
6. The released to final transition exists in the stage state model; there is
   no operator button for it in v1, so released is the resting state after
   publication.

## 9. Dispute handling window

- Teams dispute through the platform only: Submission detail > dispute
  (statement 10 to 1000 characters). The endpoint exists only after feedback
  is released for that submission and marks it disputed.
- Announce the window with the release announcement. Window length:
  [TO BE FILLED: dispute window length, announced with results].
- Resolve in Operations > Disputes: outcome upheld or rejected, resolution
  note, and optionally adjust_points, which appends an adjustment ledger entry
  with reason DISPUTE_UPHELD referencing the submission. Resolutions are
  final per the published rules. Every resolve is audited.

## 10. Results publication and archiving

- After release, publish the standing announcement (info) with the results
  location, the dispute window, and thanks.
- Export for the record: Operations > Exports, results, submissions, and
  ledger CSVs. Run Audit > Verify chain one last time and store the output
  with the exports.
- Retention follows `docs/PRIVACY_NOTICE.md`: event data is retained for the
  dispute window and club archiving, then reduced or archived.
- Post-event retro: reconcile the ground truth against unplanned findings
  (INT-02), record scoring disputes and outcomes for next year's key, and
  re-check the AI tooling decision record quarterly cadence.

## 11. Endpoint quick reference (organizer actions)

| Action | Endpoint |
| --- | --- |
| Create event | POST /v1/admin/events |
| Event status | POST /v1/admin/events/{id}/status |
| Freeze config | POST /v1/admin/events/{id}/config (freeze true) |
| Create stage | POST /v1/admin/events/{id}/stages |
| Stage control | POST /v1/admin/stages/{id}/control |
| Evaluate or release | POST /v1/admin/stages/{id}/release |
| Announce | POST /v1/admin/events/{id}/announcements |
| Time adjustment | POST /v1/admin/events/{id}/time-adjustment |
| Readiness | GET/POST /v1/admin/events/{id}/readiness[/{key}] |
| Live ops | GET /v1/admin/events/{id}/live |
| Health | GET /v1/admin/system/health (public probe: GET /health) |
| Audit | GET /v1/admin/audit, GET /v1/admin/audit/verify |
| Exports | GET /v1/admin/events/{id}/export/{results,submissions,ledger} |
| Disputes | GET /v1/admin/disputes, POST /v1/admin/disputes/{id}/resolve |
| Manual adjustment | POST /v1/admin/adjustments (admin role) |
| Staff registration | POST /v1/auth/register (admin + step-up; administrator-assigned password) |
| Participant registration | POST /v1/admin/events/{id}/participants (admin + step-up; team assignment) |
| Participant password reset | POST /v1/admin/users/{id}/password-reset (admin + step-up) |
| Test results | POST /v1/admin/challenges/{id}/ground-truth/test-results (step-up) |
