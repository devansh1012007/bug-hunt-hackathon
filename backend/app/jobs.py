"""Durable PostgreSQL-backed job queue (architecture 8.2, ADR-003).

- Claiming uses SELECT ... FOR UPDATE SKIP LOCKED with a lease; the claim is
  committed before work starts; long work never holds a row lock.
- Delivery is at-least-once; every handler is idempotent (unique constraints
  and claim tables enforce exactly-once business outcomes).
- Bounded retries with backoff; after max attempts the job goes to dead state
  and is surfaced in the admin health view.
"""

from __future__ import annotations

import traceback
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import app_session, uuid7

RETRY_BACKOFF_S = [10, 60, 300, 900]



def stage_release_blockers(session: Session, stage_id: str) -> dict[str, int]:
    """Return work that must finish before participant feedback can be published.

    Job rows stay active until their handler's database transaction has committed.
    Including queued, running, and dead jobs makes this a fail-closed barrier for
    at-least-once delivery: an expired/dead scoring task must be recovered or
    explicitly resolved before the event can publish a partial ledger.
    """
    pending_jobs = int(
        session.execute(
            text(
                "SELECT count(*) FROM app.jobs j "
                "WHERE j.status IN ('queued', 'running', 'dead') AND ("
                "  (j.kind IN ('evaluate_submission', 'evaluate_level_submission', "
                "               'verify_flag', 'apply_review_outcome', 'apply_test_results') "
                "   AND EXISTS (SELECT 1 FROM app.submissions s "
                "               WHERE s.id::text = j.payload ->> 'submission_id' "
                "                 AND s.stage_id = CAST(:s AS uuid))) "
                "  OR (j.kind = 'release_feedback' "
                "      AND j.payload ->> 'stage_id' = :stage_id "
                "      AND COALESCE(j.payload ->> 'publish', 'false') <> 'true')"
                ")"
            ),
            {"s": stage_id, "stage_id": stage_id},
        ).scalar_one()
    )
    unresolved_submissions = int(
        session.execute(
            text(
                "SELECT count(*) FROM app.submissions "
                "WHERE stage_id = CAST(:s AS uuid) AND status IN ('needs_review', 'in_review')"
            ),
            {"s": stage_id},
        ).scalar_one()
    )
    open_review_items = int(
        session.execute(
            text(
                "SELECT count(*) FROM app.review_items ri "
                "JOIN app.submissions s ON s.id = ri.submission_id "
                "WHERE s.stage_id = CAST(:s AS uuid) AND ri.status <> 'decided' "
                "AND COALESCE(ri.reason, '') <> 'auto_accept_confirm'"
            ),
            {"s": stage_id},
        ).scalar_one()
    )
    return {
        "pending_scoring_jobs": pending_jobs,
        "unresolved_submissions": unresolved_submissions,
        "open_review_items": open_review_items,
    }


def _release_is_blocked(blockers: dict[str, int]) -> bool:
    return any(blockers.values())

def enqueue(session: Session, kind: str, payload: dict, *, max_attempts: int = 5) -> str:
    job_id = str(uuid7())
    session.execute(
        text(
            "INSERT INTO app.jobs (id, kind, payload, status, max_attempts) "
            "VALUES (:id, :kind, CAST(:payload AS jsonb), 'queued', :max_attempts)"
        ),
        {
            "id": job_id,
            "kind": kind,
            "payload": __import__("json").dumps(payload, default=str),
            "max_attempts": max_attempts,
        },
    )
    return job_id


def claim_next(session: Session, *, lease_s: int = 120, kinds: list[str] | None = None) -> dict | None:
    now = datetime.now(UTC)
    kind_filter = "AND kind = ANY(:kinds)" if kinds else ""
    params: dict = {"now": now, "lease": lease_s}
    if kinds:
        params["kinds"] = kinds
    row = (
        session.execute(
            text(
                # The only dynamic SQL fragment is one of two fixed literals
                # ("AND kind = ANY(:kinds)" or ""); every value crosses only as a bind parameter.
                "UPDATE app.jobs SET status='running', lease_until = :lease, attempts = attempts + 1, updated_at = :now "  # nosec B608
                "WHERE id = (SELECT id FROM app.jobs WHERE status='queued' AND run_after <= :now "
                "AND (lease_until IS NULL OR lease_until < :now) "
                + kind_filter
                + " ORDER BY run_after, created_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
                "RETURNING id, kind, payload, attempts, max_attempts"
            ),
            {**params, "lease": now + timedelta(seconds=lease_s)},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def complete(session: Session, job_id: str) -> None:
    session.execute(
        text("UPDATE app.jobs SET status='done', lease_until=NULL, updated_at=:now WHERE id=:id"),
        {"now": datetime.now(UTC), "id": job_id},
    )


def fail(session: Session, job_id: str, error: str) -> None:
    row = session.execute(
        text("SELECT attempts, max_attempts, payload, kind FROM app.jobs WHERE id=:id FOR UPDATE"),
        {"id": job_id},
    ).first()
    if not row:
        return
    attempts, max_attempts = int(row.attempts), int(row.max_attempts)
    if attempts >= max_attempts:
        session.execute(
            text("UPDATE app.jobs SET status='dead', last_error=:e, lease_until=NULL, updated_at=:now WHERE id=:id"),
            {"e": error[:2000], "now": datetime.now(UTC), "id": job_id},
        )
    else:
        backoff = RETRY_BACKOFF_S[min(attempts - 1, len(RETRY_BACKOFF_S) - 1)]
        session.execute(
            text(
                "UPDATE app.jobs SET status='queued', last_error=:e, run_after=:ra, lease_until=NULL, updated_at=:now WHERE id=:id"
            ),
            {
                "e": error[:2000],
                "ra": datetime.now(UTC) + timedelta(seconds=backoff),
                "now": datetime.now(UTC),
                "id": job_id,
            },
        )


def requeue_expired_leases(session: Session) -> int:
    """Worker crash recovery: jobs whose lease expired go back to queued."""
    res = session.execute(
        text(
            "UPDATE app.jobs SET status='queued', lease_until=NULL, updated_at=:now "
            "WHERE status='running' AND lease_until < :now"
        ),
        {"now": datetime.now(UTC)},
    )
    return res.rowcount or 0


# --- handler registry ---------------------------------------------------------


HANDLERS: dict[str, callable] = {}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn

    return deco


def run_one_job() -> bool:
    """Claim and run exactly one job. Returns True when a job was processed."""
    with app_session() as session:
        job = claim_next(session)
        if not job:
            return False
        job_id = str(job["id"])
        kind = job["kind"]
        fn = HANDLERS.get(kind)
        if fn is None:
            fail(session, job_id, f"no handler for kind {kind}")
            return True
    # work happens OUTSIDE the claim transaction (architecture 8.2)
    try:
        fn(job["payload"])
        with app_session() as session:
            complete(session, job_id)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        with app_session() as session:
            fail(session, job_id, f"{e.__class__.__name__}: {e}")
    return True


def drain(max_jobs: int = 1000) -> int:
    n = 0
    for _ in range(max_jobs):
        if not run_one_job():
            break
        n += 1
    return n
