"""Server-side auto-submit at level deadline (baseline 7.5).

A level stage whose effective execution close has passed (ends_at + time
adjustments + grace) must not depend on the participant's browser. This
module submits the latest server-saved draft for every participant still
inside that level, marks the per-level status `auto_submitted`, and advances
the pointer - using the SAME transactional submission path and validation as
a manual submit, one `clock_timestamp()` read per transaction, idempotent
under retries, duplicate scheduler runs, worker crashes and a manual submit
racing the deadline (the pointer row lock picks exactly one winner).
"""

from __future__ import annotations

import json as jsonlib
from datetime import timedelta

from sqlalchemy import text

from . import flow, security
from .audit import audit_write
from .db import app_session, uuid7
from .errors import ApiError
from .jobs import handler
from .routers.levels import MAX_LOCATIONS, FlagIn, _compute_diff_from_draft, _stage_computed_diff, _validate_flag


def _expired_levels(session) -> list[dict]:
    rows = (
        session.execute(
            text(
                "SELECT st.id, st.event_id, st.ordinal, st.ends_at, st.grace_seconds, st.config, st.status "
                "FROM app.stages st JOIN app.events e ON e.id = st.event_id "
                "WHERE st.kind = 'level' AND st.status IN ('active', 'paused') "
                "AND st.ends_at IS NOT NULL AND e.status IN ('ready', 'live', 'closed')"
            )
        )
        .mappings()
        .all()
    )
    now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    out = []
    for r in rows:
        adj = session.execute(
            text("SELECT COALESCE(SUM(delta_seconds), 0) FROM app.time_adjustments WHERE stage_id = CAST(:s AS uuid)"),
            {"s": str(r["id"])},
        ).scalar_one()
        eff_end = r["ends_at"] + timedelta(seconds=int(adj or 0) + int(r["grace_seconds"] or 0))
        if now > eff_end:
            out.append(dict(r))
    return out


def _pending_participants(session, stage_id: str, level_no: int) -> list[str]:
    rows = session.execute(
        text(
            "SELECT pp.user_id FROM app.participant_progress pp "
            "JOIN app.participant_pointers ptr ON ptr.user_id = pp.user_id "
            "WHERE pp.level_no = :l AND pp.status IN ('locked', 'available', 'in_progress') "
            "AND ptr.level_no = :l AND ptr.node IN ('explain', 'exec') "
            "AND EXISTS (SELECT 1 FROM app.team_members tm JOIN app.teams t ON t.id = tm.team_id "
            "            WHERE tm.user_id = pp.user_id AND t.event_id = pp.event_id) "
            "AND EXISTS (SELECT 1 FROM app.stages st2 WHERE st2.id = CAST(:s AS uuid)) "
            "AND pp.event_id = (SELECT event_id FROM app.stages WHERE id = CAST(:s AS uuid)) "
            "ORDER BY pp.user_id"
        ),
        {"l": int(level_no), "s": stage_id},
    ).all()
    return [str(r[0]) for r in rows]


def _auto_payload_for(
    session, member: dict, user_id: str, level_no: int, challenge_version_id: str, cfg: dict
) -> tuple[dict, dict | None, str]:
    """Build the auto-submit submission payload from the server-saved draft.

    Returns (payload, artifact_meta, note). Invalid draft flags are dropped
    and recorded; empty work yields an explicit empty submission.
    """
    draft_row = (
        session.execute(
            text("SELECT payload FROM app.level_drafts WHERE author_id = CAST(:u AS uuid) AND level_no = :l"),
            {"u": user_id, "l": level_no},
        )
        .mappings()
        .first()
    )
    draft = (draft_row["payload"] or {}) if draft_row else {}
    note = "auto-submitted at deadline from the server-saved draft"
    if level_no <= 2:
        flags_out = []
        dropped = 0
        for raw in (draft.get("flags") or [])[: int(cfg.get("max_flags_per_level", 12))]:
            try:
                flag = FlagIn(
                    title=str(raw.get("title") or ""),
                    category=str(raw.get("category") or ""),
                    description=str(raw.get("description") or ""),
                    evidence=raw.get("evidence"),
                    claimed_severity=raw.get("claimed_severity"),
                    locations=[
                        {
                            "file_path": str(loc.get("file_path") or ""),
                            "line_start": int(loc.get("line_start") or 0),
                            "line_end": int(loc.get("line_end") or 0),
                        }
                        for loc in (raw.get("locations") or [])[:MAX_LOCATIONS]
                    ],
                )
                flags_out.append(_validate_flag(session, flag, level_no, challenge_version_id, cfg))
            except (ApiError, ValueError, TypeError, KeyError, IndexError):
                dropped += 1
        if dropped:
            note += f"; {dropped} draft flag(s) failed validation and were dropped"
        return (
            {
                "level_no": level_no,
                "kind": "level_report",
                "flags": flags_out,
                "confirm_empty": True,
                "empty": not flags_out,
                "auto": True,
                "auto_note": note,
            },
            None,
            note,
        )
    # Levels 3-4: compute the diff of the saved workspace against the frozen base
    diff = _compute_diff_from_draft(session, user_id, level_no, challenge_version_id)
    if diff.strip():
        meta = _stage_computed_diff(session, member, user_id, level_no, diff)
        payload = {
            "level_no": level_no,
            "kind": "level_artifact",
            "flags": [],
            "empty": False,
            "auto": True,
            "auto_note": note,
            "artifact": {
                "id": meta["id"],
                "kind": meta["kind"],
                "filename": meta["filename"],
                "sha256": meta["sha256"],
                "size_bytes": int(meta["size_bytes"]),
                "storage_key": meta["storage_key"],
            },
        }
        return payload, meta, note
    empty_note = note + "; no saved changes existed, recorded as an explicit empty submission"
    return (
        {
            "level_no": level_no,
            "kind": "level_artifact",
            "flags": [],
            "empty": True,
            "auto": True,
            "auto_note": empty_note,
        },
        None,
        empty_note,
    )


def _auto_submit_one(member: dict, user_id: str, level_no: int, stage: dict) -> str | None:
    """Transactional auto-submit for one participant. Returns the submission id
    or None when a concurrent writer (manual submit) won the race."""
    event_id = str(member["event_id"])
    with app_session() as s:
        ptr = flow.get_pointer_for_update(s, user_id)
        if ptr is None or int(ptr["level_no"]) != level_no or str(ptr["node"]) not in ("explain", "exec"):
            return None  # already advanced elsewhere; idempotent no-op
        st = flow.get_level_status(s, user_id, level_no)
        if st in flow.DONE_STATUSES:
            return None
        choice = s.execute(
            text("SELECT challenge_version_id FROM app.team_repo_choices WHERE team_id = CAST(:t AS uuid)"),
            {"t": str(member["team_id"])},
        ).first()
        stage_state = (
            s.execute(
                text(
                    "SELECT status, results_published_at FROM app.stages "
                    "WHERE id = CAST(:s AS uuid) FOR UPDATE"
                ),
                {"s": str(stage["id"])},
            )
            .mappings()
            .first()
        )
        if (
            stage_state is None
            or stage_state["status"] not in ("active", "paused")
            or stage_state["results_published_at"] is not None
        ):
            return None
        now = s.execute(text("SELECT clock_timestamp()")).scalar_one()
        cfg = stage["config"] or {}
        if choice is None:
            # repo never locked and the window closed: record the miss, do not fabricate a submission
            flow.set_level_status(s, user_id, level_no, "missed")
            audit_write(
                s,
                actor_id=None,
                actor_role="system",
                action="level.missed_no_lock",
                entity_type="participant_progress",
                entity_id=user_id,
                request_id="auto-submit",
                details={"level": level_no},
                event_scope=f"event:{event_id}",
            )
            nxt = flow.next_position(level_no, "exec")
            assert nxt is not None
            flow.advance_pointer(s, user_id, nxt[0], nxt[1])
            return "missed"
        challenge_version_id = str(choice[0])
        payload, artifact_meta, note = _auto_payload_for(s, member, user_id, level_no, challenge_version_id, cfg)
        submission_id = str(uuid7())
        receipt = security.receipt_code(submission_id, now)
        primary = (payload.get("flags") or [None])[0] if payload.get("flags") else None
        s.execute(
            text(
                "INSERT INTO app.submissions (id, tenant_id, event_id, stage_id, challenge_version_id, team_id, "
                "author_id, kind, idempotency_key, payload, payload_hash, status, receipt_code, title, file_path, "
                "line_start, line_end, received_at) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:ev AS uuid), CAST(:st AS uuid), CAST(:cv AS uuid), "
                "CAST(:team AS uuid), CAST(:au AS uuid), :kind, :idem, CAST(:payload AS jsonb), :ph, 'received', "
                ":receipt, :title, :fp, :ls, :le, :received_at)"
            ),
            {
                "id": submission_id,
                "t": str(member["tenant_id"]),
                "ev": event_id,
                "st": str(stage["id"]),
                "cv": challenge_version_id,
                "team": str(member["team_id"]),
                "au": user_id,
                "kind": payload["kind"],
                "idem": f"auto-{submission_id}",
                "payload": jsonlib.dumps(payload, default=str),
                "ph": security.canonical_payload_hash(payload),
                "receipt": receipt,
                "title": f"Level {level_no} auto-submission",
                "fp": primary.get("file_path") if primary else None,
                "ls": primary.get("line_start") if primary else None,
                "le": primary.get("line_end") if primary else None,
                "received_at": now,
            },
        )
        if artifact_meta is not None:
            s.execute(
                text(
                    "UPDATE app.submission_artifacts SET status = 'submitted', submission_id = CAST(:sid AS uuid) "
                    "WHERE id = CAST(:a AS uuid)"
                ),
                {"sid": submission_id, "a": str(artifact_meta["id"])},
            )
        from .jobs import enqueue

        enqueue(s, "evaluate_level_submission", {"submission_id": submission_id, "level_no": level_no})
        enqueue(s, "refresh_leaderboard", {"stage_id": str(stage["id"])})
        audit_write(
            s,
            actor_id=None,
            actor_role="system",
            action="level.auto_submitted",
            entity_type="submission",
            entity_id=submission_id,
            request_id="auto-submit",
            details={"level": level_no, "receipt": receipt, "note": note[:200]},
            event_scope=f"event:{event_id}",
        )
        nxt = flow.next_position(level_no, "exec")
        assert nxt is not None
        flow.set_level_status(s, user_id, level_no, "auto_submitted", submission_id=submission_id, submitted_at=now)
        flow.advance_pointer(s, user_id, nxt[0], nxt[1])
        if nxt[1] == "explain" and nxt[0] <= flow.LEVEL_COUNT:
            flow.set_level_status(s, user_id, nxt[0], "locked")
        return submission_id


def scan_and_submit() -> int:
    """Scan every expired level and auto-submit its pending participants."""
    count = 0
    with app_session() as session:
        expired = _expired_levels(session)
        pending: list[tuple[dict, str, int, dict]] = []
        for stage in expired:
            stage_info = dict(stage)
            for user_id in _pending_participants(session, str(stage["id"]), int(stage["ordinal"])):
                member = (
                    session.execute(
                        text(
                            "SELECT tm.team_id, t.event_id, e.tenant_id "
                            "FROM app.team_members tm JOIN app.teams t ON t.id = tm.team_id "
                            "JOIN app.events e ON e.id = t.event_id WHERE tm.user_id = CAST(:u AS uuid) LIMIT 1"
                        ),
                        {"u": user_id},
                    )
                    .mappings()
                    .first()
                )
                if member is not None:
                    pending.append((dict(member), user_id, int(stage["ordinal"]), stage_info))
    for member, user_id, level_no, stage in pending:
        result = _auto_submit_one(member, user_id, level_no, stage)
        if result is not None:
            count += 1
    return count


@handler("auto_submit_levels")
def auto_submit_levels(payload: dict) -> None:
    scan_and_submit()
