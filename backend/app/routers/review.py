"""Reviewer endpoints: claim from the queue, inspect the report, record a decision.

Decisions are auditable and route into the scoring ledger (ADM-RV-01..03,
ADM-RV-08). Participant content is served as plain text only (EVA-05); the UI
must never render it as HTML/Markdown.
"""

from __future__ import annotations

import json
from datetime import UTC

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..audit import audit_write
from ..db import app_session, uuid7
from ..deps import Principal, require_staff
from ..errors import ApiError
from ..jobs import enqueue

router = APIRouter(tags=["review"])

CLAIM_TIMEOUT_MIN = 10


@router.get("/review/queue")
def review_queue(p: Principal = Depends(require_staff())):
    with app_session() as s:
        rows = (
            s.execute(
                text(
                    "SELECT ri.id, ri.status, ri.priority, ri.created_at, ri.claimed_by, ri.claimed_at, "
                    "s.id AS submission_id, s.kind, s.title, s.file_path, s.line_start, s.line_end, s.category, "
                    "s.received_at, s.status AS submission_status, s.flag_code IS NOT NULL AS has_flag "
                    "FROM app.review_items ri JOIN app.submissions s ON s.id = ri.submission_id "
                    "WHERE ri.status IN ('open','claimed') ORDER BY ri.priority, ri.created_at LIMIT 200"
                )
            )
            .mappings()
            .all()
        )
    items = []
    for r in rows:
        claimed_by_me = r["claimed_by"] is not None and str(r["claimed_by"]) == p.id
        items.append(
            {
                "review_item_id": str(r["id"]),
                "submission_id": str(r["submission_id"]),
                "kind": r["kind"],
                "title": r["title"],
                "file_path": r["file_path"],
                "line_range": [r["line_start"], r["line_end"]],
                "category": r["category"],
                "received_at": r["received_at"].isoformat(),
                "priority": r["priority"],
                "status": r["status"],
                "claimed_by_me": claimed_by_me,
            }
        )
    return {"queue": items}


@router.post("/review/items/{item_id}/claim")
def claim_item(item_id: str, p: Principal = Depends(require_staff())):
    with app_session() as s:
        row = (
            s.execute(
                text(
                    "SELECT ri.id, ri.status, ri.claimed_by, ri.claimed_at, ri.reason, "
                    "st.status AS stage_status, st.results_published_at "
                    "FROM app.review_items ri JOIN app.submissions s ON s.id = ri.submission_id "
                    "JOIN app.stages st ON st.id = s.stage_id "
                    "WHERE ri.id = CAST(:i AS uuid) FOR UPDATE OF ri, st"
                ),
                {"i": item_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError("NOT_FOUND")
        if row["stage_status"] in ("released", "final") or row["results_published_at"] is not None:
            raise ApiError(
                "VALIDATION_ERROR", details={"decision": "Results have already been published for this stage."}
            )
        if row["status"] == "decided":
            raise ApiError("VALIDATION_ERROR", details={"claim": "Item already decided."})
        if row["reason"] == "needs_second_review":
            prior_review = s.execute(
                text(
                    "SELECT 1 FROM app.reviews WHERE submission_id = "
                    "(SELECT submission_id FROM app.review_items WHERE id = CAST(:i AS uuid)) "
                    "AND reviewer_id = CAST(:u AS uuid) LIMIT 1"
                ),
                {"i": item_id, "u": p.id},
            ).first()
            if prior_review is not None:
                raise ApiError("AUTH_FORBIDDEN", message="A second review must be completed by a different reviewer.")
        if row["status"] == "claimed" and row["claimed_by"] is not None and str(row["claimed_by"]) != p.id:
            claimed_at = row["claimed_at"]
            from datetime import datetime, timedelta

            if claimed_at is not None and datetime.now(UTC) - claimed_at < timedelta(minutes=CLAIM_TIMEOUT_MIN):
                raise ApiError("VALIDATION_ERROR", details={"claim": "Claimed by another reviewer."})
        s.execute(
            text(
                "UPDATE app.review_items SET status='claimed', claimed_by = CAST(:u AS uuid), claimed_at = now() WHERE id = CAST(:i AS uuid)"
            ),
            {"u": p.id, "i": item_id},
        )
        s.execute(
            text(
                "UPDATE app.submissions SET status='in_review' WHERE id = (SELECT submission_id FROM app.review_items WHERE id = CAST(:i AS uuid))"
            ),
            {"i": item_id},
        )
    return {"ok": True}


@router.get("/review/items/{item_id}")
def review_detail(item_id: str, p: Principal = Depends(require_staff())):
    """Full review context: report text (plain), code candidates, evidence, history."""
    with app_session() as s:
        row = (
            s.execute(
                text(
                    "SELECT ri.id, ri.status, s.id AS submission_id, s.kind, s.title, s.payload, s.file_path, "
                    "s.line_start, s.line_end, s.category, s.received_at, s.receipt_code, s.flag_code, "
                    "s.challenge_version_id, s.team_id, s.status AS submission_status "
                    "FROM app.review_items ri JOIN app.submissions s ON s.id = ri.submission_id WHERE ri.id = CAST(:i AS uuid)"
                ),
                {"i": item_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError("NOT_FOUND")
        file_content = None
        if row["file_path"]:
            f = (
                s.execute(
                    text(
                        "SELECT content, lang, line_count FROM app.challenge_files WHERE challenge_version_id = CAST(:c AS uuid) AND path = :p"
                    ),
                    {"c": str(row["challenge_version_id"]), "p": row["file_path"]},
                )
                .mappings()
                .first()
            )
            if f is not None:
                file_content = {"content": f["content"], "lang": f["lang"], "line_count": f["line_count"]}
        history = s.execute(
            text("SELECT count(*) FROM app.submissions WHERE team_id = CAST(:t AS uuid) AND kind = 'bug_report'"),
            {"t": str(row["team_id"])},
        ).scalar_one()
    return {
        "review_item_id": str(row["id"]),
        "status": row["status"],
        "submission": {
            "id": str(row["submission_id"]),
            "kind": row["kind"],
            "title": row["title"],
            "payload": row["payload"],
            "file_path": row["file_path"],
            "line_start": row["line_start"],
            "line_end": row["line_end"],
            "category": row["category"],
            "received_at": row["received_at"].isoformat(),
            "receipt_code": row["receipt_code"],
            "flag_code": row["flag_code"],
            "status": row["submission_status"],
        },
        "file": file_content,
        "team_total_reports": int(history),
    }


class RubricScores(BaseModel):
    functionality: float = Field(ge=0, le=1, default=0)
    root_cause: float = Field(ge=0, le=1, default=0)
    code_quality: float = Field(ge=0, le=1, default=0)
    testing: float = Field(ge=0, le=1, default=0)
    evidence: float = Field(ge=0, le=1, default=0)


class DecisionIn(BaseModel):
    decision: str = Field(
        pattern="^(accept_full|accept_partial|duplicate|reject_invalid|unplanned_valid|needs_second_review)$"
    )
    rubric: RubricScores
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/review/items/{item_id}/decision")
def record_decision(item_id: str, body: DecisionIn, request: Request, p: Principal = Depends(require_staff())):
    """Reviewer decision. The ledger application happens in the evaluator job with
    the matched bug context; here we record the decision and enqueue the work."""
    with app_session() as s:
        row = (
            s.execute(
                text(
                    "SELECT ri.id, ri.status, ri.submission_id, ri.claimed_by, ri.reason, s.team_id, s.kind, s.challenge_version_id, "
                    "st.status AS stage_status, st.results_published_at "
                    "FROM app.review_items ri JOIN app.submissions s ON s.id = ri.submission_id "
                    "JOIN app.stages st ON st.id = s.stage_id "
                    "WHERE ri.id = CAST(:i AS uuid) FOR UPDATE OF ri, st"
                ),
                {"i": item_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError("NOT_FOUND")
        if row["stage_status"] in ("released", "final") or row["results_published_at"] is not None:
            raise ApiError(
                "VALIDATION_ERROR",
                details={"decision": "Results have already been published for this stage."},
            )
        if row["status"] == "decided":
            raise ApiError("VALIDATION_ERROR", details={"decision": "Already decided."})
        if row["reason"] == "needs_second_review":
            prior_review = s.execute(
                text(
                    "SELECT 1 FROM app.reviews WHERE submission_id = CAST(:sid AS uuid) "
                    "AND reviewer_id = CAST(:u AS uuid) LIMIT 1"
                ),
                {"sid": str(row["submission_id"]), "u": p.id},
            ).first()
            if prior_review is not None:
                raise ApiError("AUTH_FORBIDDEN", message="A second review must be completed by a different reviewer.")
            if body.decision == "needs_second_review":
                raise ApiError(
                    "VALIDATION_ERROR",
                    details={"decision": "A second review cannot request another second review."},
                )
        if row["status"] == "claimed" and row["claimed_by"] is not None and str(row["claimed_by"]) != p.id:
            raise ApiError("AUTH_FORBIDDEN", message="Claim this item before deciding.")

        sub_status = {
            "accept_full": "decided",
            "accept_partial": "decided",
            "duplicate": "duplicate",
            "reject_invalid": "rejected_auto",
            "unplanned_valid": "needs_review",
            "needs_second_review": "in_review",
        }[body.decision]

        review_id = str(uuid7())
        s.execute(
            text(
                "INSERT INTO app.reviews (id, submission_id, reviewer_id, decision, rubric, quality, notes) "
                "VALUES (CAST(:id AS uuid), CAST(:sid AS uuid), CAST(:u AS uuid), :d, CAST(:rub AS jsonb), :q, :n)"
            ),
            {
                "id": review_id,
                "sid": str(row["submission_id"]),
                "u": p.id,
                "d": body.decision,
                "rub": json.dumps(body.rubric.model_dump()),
                "q": 1.0 if body.decision == "accept_full" else (0.5 if body.decision == "accept_partial" else 0.0),
                "n": body.notes,
            },
        )
        s.execute(text("UPDATE app.review_items SET status='decided' WHERE id = CAST(:i AS uuid)"), {"i": item_id})
        s.execute(
            text("UPDATE app.submissions SET status=:st WHERE id = CAST(:sid AS uuid)"),
            {"st": sub_status, "sid": str(row["submission_id"])},
        )
        if body.decision == "needs_second_review":
            # The second-review task is created in the same transaction as the
            # first decision. No scoring job is queued until an independent
            # reviewer records the terminal decision.
            s.execute(
                text(
                    "INSERT INTO app.review_items (id, submission_id, status, priority, reason) "
                    "VALUES (CAST(:id AS uuid), CAST(:sid AS uuid), 'open', 1, 'needs_second_review')"
                ),
                {"id": str(uuid7()), "sid": str(row["submission_id"])},
            )
        else:
            enqueue(
                s,
                "apply_review_outcome",
                {"submission_id": str(row["submission_id"]), "review_id": review_id, "decision": body.decision},
            )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="review.decided",
            entity_type="submission",
            entity_id=str(row["submission_id"]),
            request_id=request.state.request_id,
            details={"decision": body.decision},
            event_scope="review",
        )
    return {"ok": True, "decision": body.decision}
