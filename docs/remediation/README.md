# Bug Hunt CTF remediation register

**Status:** In progress  
**Prepared:** 2026-11-28  
**Branch:** `remediation/2026-11-28-p0-p1`  
**Base:** `d991d9be423ee795c1e7246d1fbe6c4a24a7c003` (current `main` at branch creation)  
**Planning baseline:** `31720a807a5a2e1513d677673fe731b8a23c5cf6` (historical; not the branch base)

## 1. Evidence and inventory status

The source audit dataset is `docs/audit/findings_all.json` (Git blob
`6145b346cf395d252d98d55fe2bf2ec9ce846e37`), prepared against audit snapshot `f96eb5ca`.
Its provenance file states that evidence paths and line numbers are baseline-
relative and must be revalidated at the target commit.

The source file contains **389 unique source records**:
- CRITICAL: 1
- HIGH: 60
- INFO: 1
- LOW: 110
- MEDIUM: 217

The **327 MEDIUM/LOW** workstream is therefore the 217 MEDIUM + 110 LOW source
records. The old narrative audit count of 393 is not supported by this dataset.
The 389 records and their baseline evidence are preserved in
`source_inventory.json`; they are not being reported as 389 current defects.

`findings_verified_remaining.json`, which the revised plan identifies as the
source of 68 canonical active issues, is **not present in the repository tree
at this branch base**. Therefore:
- no canonical issue mapping is fabricated;
- every source record has `canonical_issue_id: null` until the companion
  register is supplied and reconciled;
- all records other than the explicitly superseded `F-PRP-01` remain
  `needs_revalidation_at_d991d9b`;
- this register is a traceability scaffold, not a declaration that WP0 has
  passed its exit gate.

## 2. P0 revalidation against the branch base

| Plan ID | Current source evidence | Status at branch base | Action |
| --- | --- | --- | --- |
| REM-001 | `backend/app/deps.py` Principal omits tenant context; rules and state selectors choose a global latest event/stage; admin event creation chooses the first tenant | **Confirmed blocker** | Implement server-derived tenant/event principal and scope every route/query; add multi-tenant/two-event negative tests |
| REM-002 | `backend/app/main.py` mounts admin routers in the participant API; `backend/app/db.py` creates `gt_engine` at import; admin routes use `gt_session` | **Confirmed blocker** | Split participant/admin/GT processes and credentials; participant runtime must not receive `DATABASE_URL_GT` |
| REM-009 | `release_results(action=evaluate)` enqueues `release_feedback`; that handler sets `feedback_released=true` unconditionally; live/frozen leaderboard reads lack a completed-publication gate | **Confirmed blocker; remediation started in this branch** | Add publication marker; publish only on explicit release; gate participant result and leaderboard routes |
| REM-010 | `my_results` adds negative penalty rows to component totals and then adds the penalty subtotal again | **Confirmed blocker; arithmetic fix started in this branch** | Sum ledger entries once and test a penalized released team |

The relevant historic source records include:
- REM-001: `F-AD8A-01`
- REM-002: `F-ARA-01`, `F-ARB-03`, `F-B02A-02`, `F-B02A-14`
- REM-009: score-release behavior requires current-code validation; see
  `backend/app/routers/admin.py`, `backend/app/evaluator.py`,
  `backend/app/routers/leaderboard.py`
- REM-010: `F-QLT-01`, `F-S03B-02`

The P0 entries are not marked closed until the regression suite passes and
the deployed staging topology proves the same behavior.

## 3. PR execution status

| Workstream | Status | Evidence |
| --- | --- | --- |
| WP0 canonical source inventory | **Partial** | 389 unique source records enumerated; companion canonical register missing |
| PR-3 score release / score aggregation | **Implemented on remediation branch; CI in progress** | Publication marker, fail-closed queue/review barrier, post-publication mutation guards, step-up UI/API, score arithmetic and regression tests |
| Review workflow | **Implemented; CI in progress** | Second-review decisions create an independent task transactionally; same reviewer is barred from claiming/deciding it |
| PR-1 tenant/event scope | **Not started** | Current principal and global selectors still violate the invariant |
| PR-2 process/credential isolation | **Not started** | Participant API process still imports privileged GT engine |
| P1 owner decisions | **Open** | See `docs/KNOWN_GAPS_AND_OWNER_DECISIONS.md` and revised plan §3 |

## 4. Verification rules

A source record is not closed by a code edit alone. Closure requires:
1. current evidence path and deterministic reproduction at the target commit;
2. regression tests, including negative and concurrent tests where relevant;
3. independent review of the final diff and migrations;
4. successful CI for the exact commit;
5. staging/deployment evidence for runtime/security boundaries.

No READY claim is made by this register.


## 5. PR review preservation and verification

The PR review was performed using the repository's `code-review` skill. The
branch is based on current `main` and is not behind it; no merge conflict
resolution was required. Authentication/sign-in implementation and migrations
0005, 0006 and 0007 were intentionally left untouched. The admin-managed
participant/staff account policy on `main` remains authoritative.

The latest code review changes additionally:
- require recent step-up for Evaluate and Release;
- block publication while queued, running or dead scoring jobs and unresolved
  review work remain;
- serialize reviewer/test-result/auto-submit writes against publication;
- create and enforce an independent second-review task;
- update the end-to-end journey to resolve every review item before release.

CI is required on the exact PR head. A green end-to-end journey alone does not
close REM-001/REM-002 or establish deployed READY evidence.
