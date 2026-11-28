Status: v1 draft for organizer review, updated 2026-11-27

Open fields: production per-level `max_points` values require organizer approval.
Every number in this document is implemented in
`backend/app/scoring.py`, `backend/app/matching.py`, `backend/app/evaluator.py`,
`backend/app/evaluator_level.py`, `backend/app/routers/levels.py`, and
`backend/seed.py`.

# Scoring Model (Fall Back, v1)

This document specifies the complete scoring model as implemented, so that a
reviewer, an auditor, or a disputing team can reconstruct any number the
platform produces. The implementation doc pair is
`docs/CHALLENGE_DESIGN_AND_GROUND_TRUTH.md` (award units) and
`docs/EVENT_OPERATIONS_PLAYBOOK.md` (release sequence).

## 1. Principles

1. Award units. The scoreable objects are the ten ground-truth bugs (275
   points total) and the two CTF flags (60 bonus points) of the Papyrus
   challenge. A team can claim each bug award unit once: the database
   enforces uniqueness on (stage_id, team_id, award_unit_id), so concurrent
   evaluations cannot double-award (DAT-01). Extra reports of an already
   claimed unit resolve to duplicate status and pay nothing, and are not
   counted as invalid.
2. Immutable ledger. Every score is a row in `app.score_ledger_entries`:
   tenant, event, stage, team, submission, award unit, component, kind,
   points (2 decimals), reason_code, actor, note. The ledger is append-only.
   Corrections append reversal or adjustment entries that reference the
   original entry; nothing is ever edited or deleted (architecture 8.3,
   DAT-01/05).
3. Determinism first. Reports are matched by a pure function of (report,
   ground truth revision, rule set, already-claimed units) that records all
   feature values (location score, category score, alias evidence, distance)
   in the ledger note, so every decision is explainable and replayable
   (DAT-05). Auto-accept is conservative; borderline cases go to human review
   (EVA-01, INT-11).
4. Server-owned truth. Deadlines, caps, cooldowns, and verdicts are enforced
   server side; feedback is hidden until release (INT-01), and the penalty is
   applied only at release.

## 2. The three components and their weights

Each accepted award unit splits its base points (the bug's points from ground
truth) into three components using the event's frozen weights
(`backend/app/scoring.py`, `compute_award_points`):

| Component | Weight (Fall Back) | Points formula | Source of the quality factor |
| --- | --- | --- | --- |
| functional_test | 0.5 | base x 0.5 x test_fraction | Organizer-recorded hidden test results |
| code_review | 0.3 | base x 0.3 x review_quality | Reviewer rubric, or the release default |
| evidence_report | 0.2 | base x 0.2 x evidence_quality | Deterministic alias evidence at match time |

Weights must sum to 1.0; the config revision endpoint validates this and
rejects anything else. The weights are event configuration, frozen at
publication (DAT-04): the seeded Fall Back event ships 0.5 / 0.3 / 0.2 and
the stage config carries a rule_set_hash over the frozen values.

Where each number comes from:

- test_fraction comes from organizer-recorded results. The organizer runs the
  repaired challenge app for a team offline and records
  (tests_passed, tests_total) per submission via the admin API (ADR-019; the
  runner is disabled, ADR-010). The `apply_test_results` job appends the
  functional_test entry with reason_code TEST_RESULTS and the note "passed
  X/Y". It is idempotent: a submission that already has the component is
  skipped. Recording requires step-up.
- review_quality comes from the reviewer rubric. A reviewer records five axes
  (functionality, root_cause, code_quality, testing, evidence), each 0 to 1
  in 0.05 steps. The score uses root_cause and code_quality only:
  review_quality = code_quality x 0.4 + root_cause x 0.6. The other three
  axes are recorded for audit and disputes but do not feed the number in v1.
  Decision effects on quality: accept_full floors review_quality at 0.5;
  accept_partial multiplies it by 0.5. The review row also stores a decision
  level quality factor: 1.0 for accept_full, 0.5 for accept_partial.
- If release arrives with accepted reports that have no reviewer score, the
  release job applies the event default (default_review_quality, 0.8) and
  marks the entry reason_code RELEASE_DEFAULT with the note "default review
  quality 0.8 applied at release (disputable)". These entries are explicitly
  disputable in the window.
- evidence_quality is computed by the matcher from the report text
  (description plus evidence): weighted keyword hits over the bug's alias
  list. A deterministic full match writes the evidence_report entry
  immediately at match time with reason_code MATCH_FULL and the note
  "bug=CODE L=.. C=.. A=.." (location, category, alias scores), using
  evidence_quality = max(alias_evidence, 0.6).

Participant display distinguishes cumulative points from evaluation score.
Points are the sum of all ledger entries, including CTF bonuses, penalties and
adjustments. A level's score is the sum of its non-CTF ledger entries and is
displayed as `score / max_points` only when `evaluation_status` is `complete`.
`max_points` is a participant-safe ceiling stored in the app stage config; the
participant API never reads ground truth to obtain it. The development seed
uses 275 for each level because every fixture bug can be claimed once per
level. Production values remain an organizer approval item.

## 3. Decision paths and what they pay

The evaluator's decision paths (deterministic matcher, then evaluator jobs):

- accept_full (auto). Location score at least 0.9, category score at least
  0.6, alias evidence at least 0.2. Claims the award unit; writes the
  evidence_report entry immediately; sets status matched_auto; enqueues a
  reviewer confirmation item (priority 7). Pays: evidence now, code_review
  from the reviewer or at release, functional_test when recorded.
- accept_partial (auto). Location aligned, category unconfirmed. No award
  claim; status needs_review; reviewer item priority 5. If the reviewer
  accepts and the unit was claimed by an earlier deterministic match, the
  code_review component applies; in v1 a reviewer acceptance of a report with
  no prior claim scores nothing automatically (manual promotion of unplanned
  or unmatched-but-valid findings is a P2 capability, INT-02; such reports
  route to the organizers with priority 2 via unplanned_valid).
- reject_invalid (auto). Structural rejections only: span over max_span (25),
  missing file, or no location or alias evidence with a description under 20
  characters. Status rejected_auto; counted as invalid; feeds the penalty at
  release. No ledger entry at match time. A reviewer reject_invalid decision
  lands in the same rejected_auto status and is counted by the penalty in the
  same way, which is why reviewers are told to reserve it for definitively
  invalid reports.
- needs_review (auto). Plausible but unmatched reports (description of at
  least 20 characters, no candidate in the same file) and borderline matches
  go to the queue with priority 5. Never auto-penalized (INT-02).
- duplicate. Award unit already claimed by the team. Status duplicate; no
  points; not invalid; no penalty.
- Reviewer decisions from the queue (accept_full, accept_partial, duplicate,
  reject_invalid, unplanned_valid, needs_second_review) map to submission
  statuses and ledger effects as above; needs_second_review returns the
  submission to in_review and closes the item (a second item is not
  auto-created in v1; flag it to the organizers).

## 4. CTF bonus and caps

- Each flag is worth its ground-truth points (FLAG-1 and FLAG-2, 30 each).
  Verification is by HMAC hash comparison at evaluation time; plaintext
  never rests in the app schema.
- Per-flag max_awards (default 99) bounds how many teams can be paid for one
  flag; further submissions of the same flag resolve to duplicate.
- Per-team per-stage cap: ctf_bonus_cap_points, 100 for Fall Back. The verify
  job pays min(flag points, cap minus the team's ctf_bonus total so far) and
  notes "(capped)" on the entry when truncated. Reason FLAG_VERIFIED.
- Bonus entries are component ctf_bonus, kind bonus. They never interact with
  the main-score components or the penalty cap.
- Concurrent verification is serialized twice before the max_awards count and
  claim: the claiming team's row is locked `FOR UPDATE` for the bonus-cap read
  and ledger append, and a transaction-scoped advisory lock scoped to the
  ground-truth flag (per challenge version; gt key uniqueness is
  `(challenge_version_id, flag_key)`, so the pool is one flag row of one
  version, never the bare `flag_key` across versions) serializes teams racing
  for the same award unit. The queue is at-least-once, so a redelivered
  verify_flag job finds the submission's existing ctf_bonus ledger entry and
  leaves the already-paid submission untouched instead of relabeling it
  duplicate.

## 5. Penalty model

Applied once, at release only (INT-01/INT-11; stage config
penalty.applies = at_release_only):

- Basis: reports in rejected_auto status at release time (definitively invalid
  only; needs_review, duplicate, and disputed reports never count).
- Per report: lowest severity base 10 points x fp_factor 0.25 = 2.5 points per
  invalid report. The claimed severity is ignored; the base is the lowest
  severity value by design, so the penalty stays small.
- Cap: cap_fraction 0.2 of the stage maximum (sum of ground-truth bug points
  assigned to the stage; 275 for Fall Back) = 55 points. The team's penalty is
  min(2.5 x invalid_count, 55).
- The release job appends one penalty entry per team: component penalty, kind
  penalty, negative points, reason INVALID_REPORT_PENALTY, note with the count
  and the cap.
- Never shown live: before release the participant list collapses all
  in-flight statuses to "received", so teams cannot probe the evaluator's
  verdicts (and template-report probing is a rules violation).

## 6. Tie-break order

Leaderboard builds rank rows with this sort key (INT-12), applied at equal
totals in this order:

1. Total score, descending (sum of all ledger entries, penalties included).
2. Fewer invalid reports (count of rejected_auto bug reports).
3. Earlier last accepted finding (max received time among the team's
   submissions carrying any non-penalty ledger entry; a team with none sorts
   last).

Snapshots are versioned and served per the stage's leaderboard_mode. For
participant requests, every mode is additionally gated by the
`results_published_at` marker, which is committed only after the explicit
organizer release job has finished writing the ledger and feedback flags.
This prevents `live` or `frozen` from becoming a pre-release score bypass.
After publication, the hidden mode stops the pre-release live view,
frozen serves snapshots delayed by `leaderboard_delay_s` (600 seconds), and
live serves the newest snapshot. The release job enqueues the public snapshot
in the same transaction that records publication completion.

## 7. What is configurable, and when it freezes

Per event (config revisions, each with a reason, frozen at publication,
DAT-04):

- scoring.weights (must sum to 1.0), scoring.ctf_bonus_cap_points,
  scoring.default_review_quality.
- features: runner_enabled, ai_assessment_enabled, telemetry_enabled (kill
  switches, off for Fall Back).

Per stage (frozen into rule_set_hash at creation):

- feedback_mode (hidden), leaderboard_mode (hidden) and leaderboard_delay_s
  (600), max_points (participant-safe evaluation ceiling), max_span (25),
  max_reports_per_file (12), max_reports_per_round (80), file_cooldown_s (20),
  location_tolerance_lines (2), ctf_bonus_cap points (100), penalty
  (fp_factor 0.25, cap_fraction 0.2, applies at_release_only).

After publication and stage seal, scoring-relevant values do not change; the
audit trail records the freeze and any pre-freeze revision with its reason.

## 8. Rescoring rules

- The ledger is append-only. There is no update or delete path for ledger
  rows in the codebase.
- Corrections are new entries: a reversal references the original entry
  (add_ledger_entry accepts reverses_entry_id), and adjustments are
  self-standing entries with a reason.
- Dispute upheld with a point correction appends component adjustment, kind
  adjustment, reason DISPUTE_UPHELD, referencing the disputed submission and
  the resolution note.
- Manual corrections by the platform admin append reason MANUAL with the
  admin's reason text (admin role only; organizer role cannot).
- Because totals are SUM over the ledger, reversals must offset the original
  amounts; the entry notes make the chain readable in the exported CSV.

## 9. Dispute flow

1. After release, a participant files a dispute on their own submission
   (statement 10 to 1000 characters). The endpoint exists only after feedback
   is released for the stage; the submission is marked disputed.
2. Organizers see the filing (status, receipt, statement) in Operations >
   Disputes and resolve with outcome upheld or rejected, a resolution note,
   and optionally adjust_points for upheld cases (section 8). Resolutions are
   final per the published rules.
3. RELEASE_DEFAULT review entries and penalty entries are the expected dispute
   targets; both carry their provenance in reason codes and notes by design.

## 10. Worked numeric examples

### Example A: one accepted bug, full quality

Team A files a report on BUG-05 (delete-target, 40 points). The matcher
auto-accepts: location score 1.0 (same file, overlapping range), category
score 1.0 (data_handling chosen), alias evidence 1.0 (all aliases present in
the description and evidence). The organizer later records functional test
results of 1/1 for the submission. No reviewer score lands before release, so
the release default applies.

- functional_test = 40 x 0.5 x (1/1) = 20.00 (reason TEST_RESULTS)
- code_review = 40 x 0.3 x 0.8 = 9.60 (reason RELEASE_DEFAULT, disputable)
- evidence_report = 40 x 0.2 x max(1.0, 0.6) = 8.00 (reason MATCH_FULL)
- Team A total from this unit = 20.00 + 9.60 + 8.00 = 37.60 of a possible 40.

Variant: the reviewer instead scores code_quality 0.8 and root_cause 1.0
before release. review_quality = 0.8 x 0.4 + 1.0 x 0.6 = 0.92, so
code_review = 40 x 0.3 x 0.92 = 11.04 and the unit pays 39.04. The reviewer
entry (reason ACCEPT_FULL) and the default entry cannot both exist: release
only fills the default when the component is absent.

### Example B: a flag team with two invalid reports

Team B submits FLAG-1 (30) and FLAG-2 (30), both verified, plus 8 invalid
reports over the round. Flag cap 100; penalty cap 55.

- ctf_bonus = 30 + 30 = 60.00 (cap 100 not reached; per-flag awards well
  under max_awards 99)
- penalty = min(8 x 2.5, 55) = 20.00, written as -20.00
  (reason INVALID_REPORT_PENALTY, note "8 invalid report(s), capped at 55.0")
- Team B total = 60.00 - 20.00 = 40.00.

Cap illustration: had Team B also verified a hypothetical 50 point third
flag, it would pay min(50, 100 - 60) = 40.00 and the entry would be noted
"(capped)". Had the same team collected 30 invalid reports, the penalty would
be min(75, 55) = 55.00, exactly the cap.

### Example C: tie-break in action

Team A totals 37.60 with 1 invalid report and last accepted at 14:02. Team B
totals 37.60 with 0 invalid reports. B ranks above A on the second criterion
(fewer invalid). If B also had 1 invalid, the earlier last accepted time wins.
The leaderboard table prints this order under the ranking so participants can
verify it.
