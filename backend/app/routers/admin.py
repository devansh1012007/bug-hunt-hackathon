"""Organizer/admin endpoints.

Sections implemented (spec ADM-*): events + lifecycle, config freeze,
stages (arm/start/pause/resume/extend/end), teams, participants + invites,
challenges (upload files, freeze, seal, publish), ground truth (gt_admin
session, step-up required), announcements, kill switches, exports, audit
viewer, readiness, system health, scoring test results, rescoring/adjustments,
dispute resolution, result release.

Every mutation writes an audit entry. Sensitive ground-truth operations require
step-up (deps.step_up_verified) and use the gt_engine session.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..audit import audit_verify, audit_write
from ..config import settings
from ..db import app_session, gt_session, health_probe, uuid7
from ..deps import Principal, require_organizer, step_up_verified
from ..errors import ApiError
from ..jobs import enqueue
from ..scoring import add_ledger_entry

router = APIRouter(tags=["admin"])


# --- events -------------------------------------------------------------------


class EventIn(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    slug: str = Field(min_length=3, max_length=80, pattern="^[a-z0-9-]+$")
    starts_at: str | None = None
    duration_minutes: int = 180
    max_participants: int = 50
    max_teams: int = 12
    timezone: str = "Asia/Kolkata"


@router.post("/admin/events", status_code=201)
def create_event(body: EventIn, request: Request, p: Principal = Depends(require_organizer)):
    with app_session() as s:
        tenant = s.execute(text("SELECT id FROM app.tenants ORDER BY created_at LIMIT 1")).first()
        if tenant is None:
            raise ApiError("VALIDATION_ERROR", details={"tenant": "No tenant exists. Run the seed script."})
        ev_id = str(uuid7())
        s.execute(
            text(
                "INSERT INTO app.events (id, tenant_id, slug, name, duration_minutes, max_participants, max_teams, config) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :slug, :name, :dur, :mp, :mt, CAST(:cfg AS jsonb))"
            ),
            {
                "id": ev_id,
                "t": str(tenant[0]),
                "slug": body.slug,
                "name": body.name,
                "dur": body.duration_minutes,
                "mp": body.max_participants,
                "mt": body.max_teams,
                "cfg": json.dumps(
                    {
                        "features": {
                            "runner_enabled": settings.runner_enabled,
                            "ai_assessment_enabled": settings.ai_assessment_enabled,
                        },
                        "scoring": {
                            "weights": {"functional_test": 0.5, "code_review": 0.3, "evidence_report": 0.2},
                            "ctf_bonus_cap_points": 100,
                        },
                        "public_summary": {},
                        "capacity_note": "Provisional caps: 50 participants / 12 teams. Must be load-tested before the event (ADR-011).",
                    }
                ),
            },
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="event.created",
            entity_type="event",
            entity_id=ev_id,
            request_id=request.state.request_id,
        )
    return {"event_id": ev_id}


EVENT_TRANSITIONS = {
    "draft": {"configured"},
    "configured": {"ready", "draft"},
    "ready": {"live"},
    "live": {"closed"},
    "closed": {"finalized"},
    "finalized": {"archived"},
}


@router.post("/admin/events/{event_id}/status")
def set_event_status(event_id: str, body: dict, request: Request, p: Principal = Depends(require_organizer)):
    target = body.get("status")
    with app_session() as s:
        cur = s.execute(
            text("SELECT status FROM app.events WHERE id = CAST(:e AS uuid) FOR UPDATE"), {"e": event_id}
        ).first()
        if cur is None:
            raise ApiError("NOT_FOUND")
        if target not in EVENT_TRANSITIONS.get(cur[0], set()):
            raise ApiError("VALIDATION_ERROR", details={"transition": f"Cannot move from {cur[0]} to {target}."})
        if target == "live":
            # readiness gate: required items must be checked (ADM-EV-05)
            pending = s.execute(
                text(
                    "SELECT count(*) FROM app.readiness_items WHERE event_id = CAST(:e AS uuid) AND state = 'unchecked'"
                ),
                {"e": event_id},
            ).scalar_one()
            if int(pending) > 0:
                raise ApiError("VALIDATION_ERROR", details={"readiness": f"{pending} readiness items are unchecked."})
            s.execute(
                text(
                    "UPDATE app.events SET starts_at = COALESCE(starts_at, now()), ends_at = now() + make_interval(mins => duration_minutes) WHERE id = CAST(:e AS uuid)"
                ),
                {"e": event_id},
            )
        s.execute(text("UPDATE app.events SET status=:st WHERE id = CAST(:e AS uuid)"), {"st": target, "e": event_id})
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="event.status_changed",
            entity_type="event",
            entity_id=event_id,
            request_id=request.state.request_id,
            details={"from": cur[0], "to": target},
        )
    return {"ok": True, "status": target}


class ConfigIn(BaseModel):
    config: dict
    reason: str = Field(min_length=5, max_length=500)
    freeze: bool = False


@router.post("/admin/events/{event_id}/config")
def revise_config(event_id: str, body: ConfigIn, request: Request, p: Principal = Depends(require_organizer)):
    """New config revision with reason; publication freezes the snapshot (DAT-04)."""
    w = body.config.get("scoring", {}).get("weights", {})
    if w and abs(sum(float(v) for v in w.values()) - 1.0) > 0.001:
        raise ApiError("VALIDATION_ERROR", details={"weights": "Scoring weights must sum to 1.0."})
    canonical = json.dumps(body.config, sort_keys=True, separators=(",", ":"))
    rule_set_hash = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    with app_session() as s:
        ver = s.execute(
            text("SELECT COALESCE(MAX(version), 0) FROM app.event_config_revisions WHERE event_id = CAST(:e AS uuid)"),
            {"e": event_id},
        ).scalar_one()
        s.execute(
            text(
                "INSERT INTO app.event_config_revisions (id, event_id, version, config, rule_set_hash, reason, created_by, frozen_at) "
                "VALUES (CAST(:id AS uuid), CAST(:e AS uuid), :v, CAST(:cfg AS jsonb), :h, :reason, CAST(:u AS uuid), "
                "CASE WHEN :freeze THEN now() ELSE NULL END)"
            ),
            {
                "id": str(uuid7()),
                "e": event_id,
                "v": int(ver) + 1,
                "cfg": json.dumps(body.config, default=str),
                "h": rule_set_hash,
                "reason": body.reason,
                "u": p.id,
                "freeze": body.freeze,
            },
        )
        if body.freeze:
            s.execute(
                text(
                    "UPDATE app.events SET config = CAST(:cfg AS jsonb), config_version = config_version + 1, rules_version = rules_version + 0 WHERE id = CAST(:e AS uuid)"
                ),
                {"cfg": json.dumps(body.config, default=str), "e": event_id},
            )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="event.config_revised",
            entity_type="event",
            entity_id=event_id,
            request_id=request.state.request_id,
            details={"version": int(ver) + 1, "frozen": body.freeze, "reason": body.reason},
        )
    return {"ok": True, "rule_set_hash": rule_set_hash}


@router.get("/admin/events/{event_id}/config/revisions")
def list_config_revisions(event_id: str, p: Principal = Depends(require_organizer)):
    """Return the event's current config and immutable revision history."""
    with app_session() as s:
        event = (
            s.execute(
                text("SELECT config, config_version FROM app.events WHERE id = CAST(:e AS uuid)"),
                {"e": event_id},
            )
            .mappings()
            .first()
        )
        if event is None:
            raise ApiError("NOT_FOUND")
        rows = (
            s.execute(
                text(
                    "SELECT version, config, rule_set_hash, reason, created_at, frozen_at "
                    "FROM app.event_config_revisions WHERE event_id = CAST(:e AS uuid) "
                    "ORDER BY version DESC LIMIT 100"
                ),
                {"e": event_id},
            )
            .mappings()
            .all()
        )
    return {
        "config_version": int(event["config_version"]),
        "current_config": event["config"] or {},
        "revisions": [dict(row) for row in rows],
    }


@router.get("/admin/events")
def list_events(p: Principal = Depends(require_organizer)):
    with app_session() as s:
        rows = (
            s.execute(
                text(
                    "SELECT id, slug, name, status, starts_at, ends_at, timezone, max_participants, max_teams, rules_version, config_version FROM app.events ORDER BY created_at DESC"
                )
            )
            .mappings()
            .all()
        )
    return {"events": [dict(r) for r in rows]}


# --- stages -------------------------------------------------------------------


class StageIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: str = "main"
    grace_seconds: int = 3
    config: dict = Field(default_factory=dict)


DEFAULT_STAGE_CONFIG = {
    "feedback_mode": "hidden",
    "leaderboard_mode": "hidden",
    "leaderboard_delay_s": 600,
    "max_span": 25,
    "max_reports_per_file": 12,
    "max_reports_per_round": 80,
    "file_cooldown_s": 20,
    "location_tolerance_lines": 2,
    "ctf_bonus_cap_points": 100,
    "penalty": {"fp_factor": 0.25, "cap_fraction": 0.2, "applies": "at_release_only"},
}


@router.post("/admin/events/{event_id}/stages", status_code=201)
def create_stage(event_id: str, body: StageIn, request: Request, p: Principal = Depends(require_organizer)):
    cfg = {**DEFAULT_STAGE_CONFIG, **body.config}
    canonical = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
    rsh = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    with app_session() as s:
        ordinal = s.execute(
            text("SELECT COALESCE(MAX(ordinal), 0) FROM app.stages WHERE event_id = CAST(:e AS uuid)"), {"e": event_id}
        ).scalar_one()
        st_id = str(uuid7())
        s.execute(
            text(
                "INSERT INTO app.stages (id, event_id, name, kind, ordinal, config, rule_set_hash) "
                "VALUES (CAST(:id AS uuid), CAST(:e AS uuid), :name, :kind, :ord, CAST(:cfg AS jsonb), :rsh)"
            ),
            {
                "id": st_id,
                "e": event_id,
                "name": body.name,
                "kind": body.kind,
                "ord": int(ordinal) + 1,
                "cfg": json.dumps(cfg),
                "rsh": rsh,
            },
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="stage.created",
            entity_type="stage",
            entity_id=st_id,
            request_id=request.state.request_id,
            details={"rule_set_hash": rsh},
        )
    return {"stage_id": st_id, "rule_set_hash": rsh}


STAGE_TRANSITIONS = {
    "draft": {"sealed"},
    "sealed": {"armed", "draft"},
    "armed": {"active"},
    "active": {"paused", "ended"},
    "paused": {"active", "ended"},
    "ended": {"evaluating"},
    "evaluating": {"released"},
    "released": {"final"},
}


@router.post("/admin/stages/{stage_id}/control")
def control_stage(stage_id: str, body: dict, request: Request, p: Principal = Depends(require_organizer)):
    """arm / start / pause / resume / extend / end - reason code required (ADM-LV-02)."""
    action = body.get("action")
    reason = body.get("reason", "")
    if not reason:
        raise ApiError("VALIDATION_ERROR", details={"reason": "A reason code is required for stage control."})
    mapping = {
        "seal": ("draft", "sealed"),
        "arm": ("sealed", "armed"),
        "start": ("armed", "active"),
        "resume": ("paused", "active"),
        "pause": ("active", "paused"),
        "end": ("active", "ended"),
    }
    with app_session() as s:
        st = (
            s.execute(
                text("SELECT id, status, ends_at, starts_at FROM app.stages WHERE id = CAST(:s AS uuid) FOR UPDATE"),
                {"s": stage_id},
            )
            .mappings()
            .first()
        )
        if st is None:
            raise ApiError("NOT_FOUND")
        now = s.execute(text("SELECT clock_timestamp()")).scalar_one()
        if action in ("arm", "seal"):
            _from, _to = mapping[action]
            if st["status"] != _from:
                raise ApiError("VALIDATION_ERROR", details={"transition": f"Cannot {action} from {st['status']}."})
            s.execute(text("UPDATE app.stages SET status=:to WHERE id = CAST(:s AS uuid)"), {"to": _to, "s": stage_id})
        elif action == "start":
            if st["status"] != "armed":
                raise ApiError(
                    "VALIDATION_ERROR", details={"transition": f"Arm the stage first (status is {st['status']})."}
                )
            s.execute(
                text(
                    "UPDATE app.stages SET status='active', started_at=:now, ends_at=:now + make_interval(mins => e.duration_minutes) FROM app.events e WHERE app.stages.id = CAST(:s AS uuid) AND e.id = app.stages.event_id"
                ),
                {"now": now, "s": stage_id},
            )
        elif action == "pause":
            if st["status"] != "active":
                raise ApiError("VALIDATION_ERROR", details={"transition": "Only an active stage can pause."})
            s.execute(text("UPDATE app.stages SET status='paused' WHERE id = CAST(:s AS uuid)"), {"s": stage_id})
        elif action == "resume":
            if st["status"] != "paused":
                raise ApiError("VALIDATION_ERROR", details={"transition": "Only a paused stage can resume."})
            # TIM-05: shift ends_at by the pause duration measured from the pause
            # transition (not stage start), so the promised working time is kept.
            paused_at = s.execute(
                text(
                    "SELECT created_at FROM app.stage_transitions WHERE stage_id = CAST(:s AS uuid) AND to_status = 'paused' ORDER BY created_at DESC LIMIT 1"
                ),
                {"s": stage_id},
            ).scalar_one()
            if paused_at is not None:
                s.execute(
                    text(
                        "UPDATE app.stages SET status='active', ends_at = ends_at + make_interval(secs => LEAST(7200, GREATEST(0, EXTRACT(epoch FROM (clock_timestamp() - CAST(:p AS timestamptz)))::int))) WHERE id = CAST(:s AS uuid)"
                    ),
                    {"s": stage_id, "p": paused_at},
                )
            else:
                s.execute(text("UPDATE app.stages SET status='active' WHERE id = CAST(:s AS uuid)"), {"s": stage_id})
        elif action == "extend":
            seconds = int(body.get("seconds", 0))
            if seconds <= 0 or seconds > 3600:
                raise ApiError("VALIDATION_ERROR", details={"seconds": "1..3600"})
            s.execute(
                text(
                    "UPDATE app.stages SET ends_at = ends_at + make_interval(secs => :sec) WHERE id = CAST(:s AS uuid)"
                ),
                {"sec": seconds, "s": stage_id},
            )
        elif action == "end":
            if st["status"] not in ("active", "paused"):
                raise ApiError("VALIDATION_ERROR", details={"transition": "Only an active or paused stage can end."})
            s.execute(
                text("UPDATE app.stages SET status='ended', ended_at=:now WHERE id = CAST(:s AS uuid)"),
                {"now": now, "s": stage_id},
            )
        else:
            raise ApiError("VALIDATION_ERROR", details={"action": "Unknown action."})
        s.execute(
            text(
                "INSERT INTO app.stage_transitions (id, stage_id, from_status, to_status, actor_id, reason_code) "
                "VALUES (CAST(:id AS uuid), CAST(:s AS uuid), :f, :t, CAST(:u AS uuid), :r)"
            ),
            {"id": str(uuid7()), "s": stage_id, "f": st["status"], "t": action, "u": p.id, "r": reason},
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action=f"stage.{action}",
            entity_type="stage",
            entity_id=stage_id,
            request_id=request.state.request_id,
            details={"reason": reason},
        )
    return {"ok": True, "action": action}


@router.post("/admin/stages/{stage_id}/release")
def release_results(stage_id: str, body: dict, request: Request, p: Principal = Depends(require_organizer)):
    """ENDED -> EVALUATING -> RELEASED: publishes verdicts and leaderboard (ADM-RS-04)."""
    action = body.get("action", "release")
    reason = body.get("reason", "results release")
    with app_session() as s:
        st = s.execute(
            text("SELECT status FROM app.stages WHERE id = CAST(:s AS uuid) FOR UPDATE"), {"s": stage_id}
        ).first()
        if st is None:
            raise ApiError("NOT_FOUND")
        if action == "evaluate" and st[0] != "ended":
            raise ApiError("VALIDATION_ERROR", details={"transition": "Stage must be ended first."})
        if action == "release" and st[0] not in ("ended", "evaluating"):
            raise ApiError("VALIDATION_ERROR", details={"transition": f"Cannot release from {st[0]}."})
        if action == "evaluate":
            s.execute(text("UPDATE app.stages SET status='evaluating' WHERE id = CAST(:s AS uuid)"), {"s": stage_id})
            enqueue(s, "release_feedback", {"stage_id": stage_id})
        elif action == "release":
            s.execute(text("UPDATE app.stages SET status='released' WHERE id = CAST(:s AS uuid)"), {"s": stage_id})
            enqueue(s, "release_feedback", {"stage_id": stage_id})
            enqueue(s, "refresh_leaderboard", {"stage_id": stage_id, "visibility": "public"})
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action=f"stage.{action}",
            entity_type="stage",
            entity_id=stage_id,
            request_id=request.state.request_id,
            details={"reason": reason},
        )
    return {"ok": True}


# --- teams, participants, invites ---------------------------------------------


class TeamIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)


@router.post("/admin/events/{event_id}/teams", status_code=201)
def create_team(event_id: str, body: TeamIn, request: Request, p: Principal = Depends(require_organizer)):
    import secrets

    with app_session() as s:
        ev = s.execute(
            text("SELECT max_teams FROM app.events WHERE id = CAST(:e AS uuid) FOR UPDATE"),
            {"e": event_id},
        ).first()
        if ev is None:
            raise ApiError("NOT_FOUND")
        count = s.execute(
            text("SELECT count(*) FROM app.teams WHERE event_id = CAST(:e AS uuid)"), {"e": event_id}
        ).scalar_one()
        if int(count) >= int(ev[0]):
            raise ApiError("VALIDATION_ERROR", details={"capacity": f"Team cap ({ev[0]}) reached."})
        team_id = str(uuid7())
        join_code = secrets.token_hex(4).upper()
        s.execute(
            text(
                "INSERT INTO app.teams (id, event_id, name, join_code) VALUES (CAST(:id AS uuid), CAST(:e AS uuid), :name, :jc)"
            ),
            {"id": team_id, "e": event_id, "name": body.name, "jc": join_code},
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="team.created",
            entity_type="team",
            entity_id=team_id,
            request_id=request.state.request_id,
        )
    return {"team_id": team_id, "join_code": join_code}


@router.get("/admin/events/{event_id}/teams")
def list_teams(event_id: str, p: Principal = Depends(require_organizer)):
    """List teams for one event; used by invitation and scoring controls."""
    with app_session() as s:
        if (
            s.execute(
                text("SELECT 1 FROM app.events WHERE id = CAST(:e AS uuid)"),
                {"e": event_id},
            ).first()
            is None
        ):
            raise ApiError("NOT_FOUND")
        rows = (
            s.execute(
                text(
                    "SELECT t.id, t.name, t.status, count(DISTINCT tm.user_id) AS member_count "
                    "FROM app.teams t LEFT JOIN app.team_members tm ON tm.team_id = t.id "
                    "WHERE t.event_id = CAST(:e AS uuid) "
                    "GROUP BY t.id, t.name, t.status ORDER BY t.name"
                ),
                {"e": event_id},
            )
            .mappings()
            .all()
        )
    return {"teams": [dict(row) for row in rows]}


class InviteIn(BaseModel):
    email: str
    team_id: str | None = None
    role: str = "participant"


@router.post("/admin/events/{event_id}/invites", status_code=201)
def create_invite(event_id: str, body: InviteIn, request: Request, p: Principal = Depends(require_organizer)):
    """Creates a single-use activation token. v1 returns it to the organizer
    console (email delivery is a deployment follow-up, see runbook)."""
    import secrets

    raw = secrets.token_urlsafe(32)
    with app_session() as s:
        from datetime import datetime, timedelta

        if s.execute(text("SELECT 1 FROM app.events WHERE id = CAST(:e AS uuid)"), {"e": event_id}).first() is None:
            raise ApiError("NOT_FOUND")
        if body.role not in ("participant", "reviewer", "organizer", "admin"):
            raise ApiError("VALIDATION_ERROR", details={"role": "Unsupported invite role."})
        if body.role != "participant" and p.role != "admin":
            raise ApiError("AUTH_FORBIDDEN", message="Only an admin can invite privileged accounts.")
        if body.team_id:
            team = s.execute(
                text("SELECT event_id FROM app.teams WHERE id = CAST(:t AS uuid)"), {"t": body.team_id}
            ).first()
            if team is None or str(team[0]) != event_id:
                raise ApiError("VALIDATION_ERROR", details={"team_id": "The team must belong to this event."})
            if body.role != "participant":
                raise ApiError(
                    "VALIDATION_ERROR", details={"team_id": "Only participant invites can be assigned to a team."}
                )

        s.execute(
            text(
                "INSERT INTO app.invites (id, event_id, email, token_hash, team_id, role, expires_at) "
                "VALUES (CAST(:id AS uuid), CAST(:e AS uuid), :email, :th, CAST(:t AS uuid), :role, :exp)"
            ),
            {
                "id": str(uuid7()),
                "e": event_id,
                "email": body.email.lower(),
                "th": __import__("hashlib").sha256(raw.encode()).hexdigest(),
                "t": body.team_id,
                "role": body.role,
                "exp": datetime.now(UTC) + timedelta(hours=48),
            },
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="invite.created",
            entity_type="event",
            entity_id=event_id,
            request_id=request.state.request_id,
            details={"email": body.email},
        )
    return {"invite_token": raw, "activation_url": f"/activate?invite={raw}"}


@router.get("/admin/events/{event_id}/participants")
def list_participants(event_id: str, p: Principal = Depends(require_organizer)):
    """List invitation-backed roster entries, including pending users."""
    with app_session() as s:
        rows = (
            s.execute(
                text(
                    "WITH member_rows AS ("
                    " SELECT u.id, u.email, u.name, u.role, u.status, t.id AS team_id, t.name AS team_name, "
                    " (u.status = 'active') AS activated, "
                    " COALESCE(latest_invite.created_at, u.created_at) AS created_at, NULL::timestamptz AS expires_at "
                    " FROM app.team_members tm "
                    " JOIN app.teams t ON t.id = tm.team_id "
                    " JOIN app.users u ON u.id = tm.user_id "
                    " LEFT JOIN LATERAL ("
                    "  SELECT i.used_at, i.created_at, i.expires_at FROM app.invites i "
                    "  WHERE i.event_id = t.event_id AND lower(i.email) = lower(u.email) "
                    "  ORDER BY i.created_at DESC, i.id DESC LIMIT 1"
                    " ) latest_invite ON TRUE "
                    " WHERE t.event_id = CAST(:e AS uuid)"
                    "), latest_invites AS ("
                    " SELECT DISTINCT ON (lower(i.email)) i.id, i.event_id, i.email, i.role, i.team_id, "
                    " i.used_at, i.created_at, i.expires_at "
                    " FROM app.invites i WHERE i.event_id = CAST(:e AS uuid) "
                    " ORDER BY lower(i.email), i.created_at DESC, i.id DESC"
                    "), invite_rows AS ("
                    " SELECT u.id, i.email, u.name, i.role, COALESCE(u.status, 'invited') AS status, "
                    " invited_team.id AS team_id, invited_team.name AS team_name, "
                    " i.used_at IS NOT NULL AS activated, i.created_at, i.expires_at "
                    " FROM latest_invites i LEFT JOIN app.users u ON lower(u.email) = lower(i.email) "
                    " LEFT JOIN app.teams invited_team ON invited_team.id = i.team_id "
                    " WHERE NOT EXISTS ("
                    "  SELECT 1 FROM app.team_members tm JOIN app.teams t ON t.id = tm.team_id "
                    "  WHERE tm.user_id = u.id AND t.event_id = i.event_id"
                    " )"
                    ") SELECT * FROM member_rows UNION ALL SELECT * FROM invite_rows "
                    "ORDER BY created_at DESC LIMIT 500"
                ),
                {"e": event_id},
            )
            .mappings()
            .all()
        )
    return {"participants": [dict(r) for r in rows]}


# --- challenges ----------------------------------------------------------------


class ChallengeIn(BaseModel):
    slug: str = Field(pattern="^[a-z0-9-]+$")
    title: str
    instructions_markdown: str | None = None


@router.post("/admin/events/{event_id}/challenges", status_code=201)
def create_challenge(event_id: str, body: ChallengeIn, request: Request, p: Principal = Depends(step_up_verified)):
    with app_session() as s:
        cid = str(uuid7())
        s.execute(
            text(
                "INSERT INTO app.challenge_versions (id, event_id, slug, title, version, instructions_md) "
                "VALUES (CAST(:id AS uuid), CAST(:e AS uuid), :slug, :title, 1, :inst)"
            ),
            {"id": cid, "e": event_id, "slug": body.slug, "title": body.title, "inst": body.instructions_markdown},
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="challenge.created",
            entity_type="challenge",
            entity_id=cid,
            request_id=request.state.request_id,
            event_scope=f"event:{event_id}",
        )
    return {"challenge_id": cid}


@router.get("/admin/events/{event_id}/challenges")
def list_challenge_versions(event_id: str, p: Principal = Depends(require_organizer)):
    """List safe challenge metadata; ground-truth secrets are never returned."""
    with app_session() as s:
        if (
            s.execute(
                text("SELECT 1 FROM app.events WHERE id = CAST(:e AS uuid)"),
                {"e": event_id},
            ).first()
            is None
        ):
            raise ApiError("NOT_FOUND")
        rows = (
            s.execute(
                text(
                    "SELECT id, slug, title, version, status, content_hash, frozen_at, published_at, created_at "
                    "FROM app.challenge_versions WHERE event_id = CAST(:e AS uuid) "
                    "ORDER BY created_at DESC LIMIT 200"
                ),
                {"e": event_id},
            )
            .mappings()
            .all()
        )
    return {"challenges": [dict(row) for row in rows]}


@router.post("/admin/challenges/{challenge_id}/files")
async def upload_files(
    challenge_id: str, request: Request, file: UploadFile = File(...), p: Principal = Depends(step_up_verified)
):
    """Multipart upload of one source file (path passed as filename). Content is
    stored inertly as text; never executed (SEC-03). Size and count limits apply."""
    content = await file.read()
    if len(content) > 512 * 1024:
        raise ApiError("VALIDATION_ERROR", details={"file": "Max 512 KiB per file."})
    path = file.filename or ""
    import os

    norm = os.path.normpath(path)
    if norm.startswith("..") or os.path.isabs(norm) or "\x00" in norm:
        raise ApiError("PATH_INVALID")
    text_content = content.decode("utf-8", errors="replace")
    line_count = text_content.count("\n") + 1
    with app_session() as s:
        cv = s.execute(
            text("SELECT status FROM app.challenge_versions WHERE id = CAST(:c AS uuid)"), {"c": challenge_id}
        ).first()
        if cv is None:
            raise ApiError("NOT_FOUND")
        if cv[0] not in ("draft", "reviewed"):
            raise ApiError("GROUND_TRUTH_LOCKED")
        s.execute(
            text(
                "INSERT INTO app.challenge_files (id, challenge_version_id, path, lang, content, sha256, size_bytes, line_count) "
                "VALUES (CAST(:id AS uuid), CAST(:c AS uuid), :p, :lang, :content, :sha, :size, :lines) "
                "ON CONFLICT (challenge_version_id, path) DO UPDATE SET content = EXCLUDED.content, sha256 = EXCLUDED.sha256, "
                "size_bytes = EXCLUDED.size_bytes, line_count = EXCLUDED.line_count"
            ),
            {
                "id": str(uuid7()),
                "c": challenge_id,
                "p": norm,
                "lang": _lang_for(norm),
                "content": text_content,
                "sha": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "lines": line_count,
            },
        )
        # rebuild the search line index for this file (sealed index, FE-04)
        s.execute(
            text("DELETE FROM app.challenge_file_lines WHERE challenge_version_id = CAST(:c AS uuid) AND path = :p"),
            {"c": challenge_id, "p": norm},
        )
        for i, line in enumerate(text_content.splitlines()[:5000], start=1):
            s.execute(
                text(
                    "INSERT INTO app.challenge_file_lines (id, challenge_version_id, path, line_number, line_text) "
                    "VALUES (CAST(:id AS uuid), CAST(:c AS uuid), :p, :ln, :lt)"
                ),
                {"id": str(uuid7()), "c": challenge_id, "p": norm, "ln": i, "lt": line[:500]},
            )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="challenge.file_uploaded",
            entity_type="challenge",
            entity_id=challenge_id,
            request_id=request.state.request_id,
            details={"path": norm},
            event_scope="challenge",
        )
    return {"ok": True, "path": norm, "line_count": line_count}


def _lang_for(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "text"
    return {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "html": "html",
        "css": "css",
        "json": "json",
        "md": "markdown",
        "sql": "sql",
    }.get(ext, "text")


@router.post("/admin/challenges/{challenge_id}/freeze")
def freeze_challenge(challenge_id: str, request: Request, p: Principal = Depends(step_up_verified)):
    with app_session() as s:
        cv = s.execute(
            text("SELECT status FROM app.challenge_versions WHERE id = CAST(:c AS uuid) FOR UPDATE"),
            {"c": challenge_id},
        ).first()
        if cv is None:
            raise ApiError("NOT_FOUND")
        if cv[0] not in ("draft", "reviewed"):
            raise ApiError("GROUND_TRUTH_LOCKED")
        files = s.execute(
            text("SELECT sha256 FROM app.challenge_files WHERE challenge_version_id = CAST(:c AS uuid) ORDER BY path"),
            {"c": challenge_id},
        ).all()
        if not files:
            raise ApiError("VALIDATION_ERROR", details={"files": "Upload at least one file before freezing."})
        content_hash = "sha256:" + hashlib.sha256("".join(f[0] for f in files).encode()).hexdigest()
        s.execute(
            text(
                "UPDATE app.challenge_versions SET status='sealed', content_hash=:h, frozen_at=now() WHERE id = CAST(:c AS uuid)"
            ),
            {"h": content_hash, "c": challenge_id},
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="challenge.frozen",
            entity_type="challenge",
            entity_id=challenge_id,
            request_id=request.state.request_id,
            details={"content_hash": content_hash},
            event_scope="challenge",
        )
    return {"ok": True, "content_hash": content_hash}


@router.post("/admin/challenges/{challenge_id}/publish")
def publish_challenge(challenge_id: str, body: dict, request: Request, p: Principal = Depends(require_organizer)):
    stage_id = body.get("stage_id")
    if not stage_id:
        raise ApiError("VALIDATION_ERROR", details={"stage_id": "Required."})
    with app_session() as s:
        cv = (
            s.execute(
                text("SELECT status, event_id FROM app.challenge_versions WHERE id = CAST(:c AS uuid) FOR UPDATE"),
                {"c": challenge_id},
            )
            .mappings()
            .first()
        )
        if cv is None:
            raise ApiError("NOT_FOUND")
        if cv["status"] not in ("sealed",):
            raise ApiError("GROUND_TRUTH_LOCKED")
        s.execute(
            text(
                "UPDATE app.challenge_versions SET status='published', published_at=now() WHERE id = CAST(:c AS uuid)"
            ),
            {"c": challenge_id},
        )
        s.execute(
            text(
                "INSERT INTO app.assignments (id, event_id, stage_id, challenge_version_id) VALUES (CAST(:id AS uuid), CAST(:e AS uuid), CAST(:s AS uuid), CAST(:c AS uuid))"
            ),
            {"id": str(uuid7()), "e": str(cv["event_id"]), "s": stage_id, "c": challenge_id},
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="challenge.published",
            entity_type="challenge",
            entity_id=challenge_id,
            request_id=request.state.request_id,
            details={"stage_id": stage_id},
            event_scope="challenge",
        )
    return {"ok": True}


# --- ground truth (gt_admin session; step-up required) -------------------------


class BugIn(BaseModel):
    code: str = Field(max_length=40)
    title: str
    severity: str = Field(pattern="^(low|medium|high|critical)$")
    category: str
    points: float = Field(gt=0, le=1000)
    root_cause_group: str
    explanation: str
    fix_guidance: str | None = None
    locations: list[dict] = Field(default_factory=list, max_length=8)
    aliases: list[dict] = Field(default_factory=list, max_length=12)


@router.post("/admin/challenges/{challenge_id}/ground-truth/bugs", status_code=201)
def add_bug(challenge_id: str, body: BugIn, request: Request, p: Principal = Depends(step_up_verified)):
    """Writes ground truth via the gt_admin role only (DAT-03)."""
    with app_session() as s:
        cv = s.execute(
            text("SELECT status FROM app.challenge_versions WHERE id = CAST(:c AS uuid)"), {"c": challenge_id}
        ).first()
        if cv is None:
            raise ApiError("NOT_FOUND")
        if cv[0] not in ("draft", "reviewed"):
            raise ApiError("GROUND_TRUTH_LOCKED")
    with gt_session() as gts:
        bug_id = str(uuid7())
        gts.execute(
            text(
                "INSERT INTO gt.gt_bugs (id, challenge_version_id, code, title, severity, category, points, root_cause_group, explanation, fix_guidance) "
                "VALUES (CAST(:id AS uuid), CAST(:c AS uuid), :code, :title, :sev, :cat, :pts, :rcg, :expl, :fix)"
            ),
            {
                "id": bug_id,
                "c": challenge_id,
                "code": body.code,
                "title": body.title,
                "sev": body.severity,
                "cat": body.category,
                "pts": body.points,
                "rcg": body.root_cause_group,
                "expl": body.explanation,
                "fix": body.fix_guidance,
            },
        )
        for loc in body.locations:
            gts.execute(
                text(
                    "INSERT INTO gt.gt_locations (id, bug_id, file_path, line_start, line_end, is_primary) VALUES (CAST(:id AS uuid), CAST(:b AS uuid), :fp, :ls, :le, :prim)"
                ),
                {
                    "id": str(uuid7()),
                    "b": bug_id,
                    "fp": loc["file_path"],
                    "ls": loc["line_start"],
                    "le": loc["line_end"],
                    "prim": loc.get("is_primary", True),
                },
            )
        for al in body.aliases:
            gts.execute(
                text(
                    "INSERT INTO gt.gt_aliases (id, bug_id, keyword, weight) VALUES (CAST(:id AS uuid), CAST(:b AS uuid), :kw, :w)"
                ),
                {"id": str(uuid7()), "b": bug_id, "kw": al["keyword"], "w": al.get("weight", 1.0)},
            )
        audit_write(
            gts,
            actor_id=p.id,
            actor_role=p.role,
            action="gt.bug_added",
            entity_type="gt_bug",
            entity_id=bug_id,
            request_id=request.state.request_id,
            details={"code": body.code},
            event_scope=f"challenge:{challenge_id}",
        )
    return {"bug_id": bug_id}


class FlagIn(BaseModel):
    flag_key: str = Field(max_length=40)
    code: str = Field(min_length=4, max_length=200)
    points: float = Field(gt=0, le=500)
    max_awards: int = Field(default=99, ge=1)


@router.post("/admin/challenges/{challenge_id}/ground-truth/flags", status_code=201)
def add_flag(challenge_id: str, body: FlagIn, request: Request, p: Principal = Depends(step_up_verified)):
    from ..security import flag_code_hash

    with gt_session() as gts:
        gts.execute(
            text(
                "INSERT INTO gt.gt_flags (id, challenge_version_id, flag_key, code_hash, points, max_awards) "
                "VALUES (CAST(:id AS uuid), CAST(:c AS uuid), :k, :h, :pts, :ma) "
                "ON CONFLICT (challenge_version_id, code_hash) DO NOTHING"
            ),
            {
                "id": str(uuid7()),
                "c": challenge_id,
                "k": body.flag_key,
                "h": flag_code_hash(body.code),
                "pts": body.points,
                "ma": body.max_awards,
            },
        )
        audit_write(
            gts,
            actor_id=p.id,
            actor_role=p.role,
            action="gt.flag_added",
            entity_type="gt_flag",
            entity_id=body.flag_key,
            request_id=request.state.request_id,
            event_scope=f"challenge:{challenge_id}",
        )
    return {"ok": True}


@router.post("/admin/challenges/{challenge_id}/ground-truth/test-results")
def record_test_results(challenge_id: str, body: dict, request: Request, p: Principal = Depends(step_up_verified)):
    """Organizer records offline functional-test results per submission (ADR-019):
    {submission_id, tests_passed, tests_total} -> enqueues ledger application."""
    sub_id = body.get("submission_id")
    passed = int(body.get("tests_passed", 0))
    total = int(body.get("tests_total", 0))
    if not sub_id or total <= 0 or passed < 0 or passed > total:
        raise ApiError("VALIDATION_ERROR", details={"tests": "Provide submission_id, tests_passed, tests_total."})
    with app_session() as s:
        enqueue(
            s,
            "apply_test_results",
            {"submission_id": sub_id, "tests_passed": passed, "tests_total": total, "actor_id": p.id},
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="scoring.test_results_recorded",
            entity_type="submission",
            entity_id=sub_id,
            request_id=request.state.request_id,
            details={"passed": passed, "total": total},
            event_scope="scoring",
        )
    return {"ok": True}


# --- live ops -------------------------------------------------------------------


class AnnouncementIn(BaseModel):
    severity: str = Field(default="info", pattern="^(info|warning|critical)$")
    body: str = Field(min_length=3, max_length=1000)


@router.post("/admin/events/{event_id}/announcements", status_code=201)
def create_announcement(
    event_id: str, body: AnnouncementIn, request: Request, p: Principal = Depends(require_organizer)
):
    with app_session() as s:
        s.execute(
            text(
                "INSERT INTO app.announcements (id, event_id, severity, body, created_by) VALUES (CAST(:id AS uuid), CAST(:e AS uuid), :sev, :b, CAST(:u AS uuid))"
            ),
            {"id": str(uuid7()), "e": event_id, "sev": body.severity, "b": body.body, "u": p.id},
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="announcement.created",
            entity_type="event",
            entity_id=event_id,
            request_id=request.state.request_id,
            details={"severity": body.severity},
        )
    return {"ok": True}


@router.post("/admin/events/{event_id}/time-adjustment")
def time_adjustment(event_id: str, body: dict, request: Request, p: Principal = Depends(require_organizer)):
    stage_id = body.get("stage_id")
    delta = int(body.get("delta_seconds", 0))
    reason_code = body.get("reason_code", "OTHER")
    user_id = body.get("user_id")
    if not stage_id or delta == 0:
        raise ApiError("VALIDATION_ERROR", details={"adjustment": "stage_id and nonzero delta_seconds required."})
    if reason_code not in ("ACCOMMODATION", "OUTAGE", "DEVICE_FAILURE", "OTHER"):
        raise ApiError("VALIDATION_ERROR", details={"reason_code": "ACCOMMODATION|OUTAGE|DEVICE_FAILURE|OTHER"})
    if abs(delta) > 86400:
        raise ApiError(
            "VALIDATION_ERROR", details={"delta_seconds": "Time adjustments must be within +/- 86400 seconds."}
        )
    with app_session() as s:
        stage = s.execute(
            text("SELECT 1 FROM app.stages WHERE id = CAST(:s AS uuid) AND event_id = CAST(:e AS uuid)"),
            {"s": stage_id, "e": event_id},
        ).first()
        if stage is None:
            raise ApiError("VALIDATION_ERROR", details={"stage_id": "Stage must belong to the selected event."})
        if user_id:
            participant = s.execute(
                text(
                    "SELECT 1 FROM app.team_members tm "
                    "JOIN app.teams t ON t.id = tm.team_id "
                    "JOIN app.users u ON u.id = tm.user_id "
                    "WHERE tm.user_id = CAST(:u AS uuid) AND t.event_id = CAST(:e AS uuid) "
                    "AND u.role = 'participant' AND u.status = 'active'"
                ),
                {"u": user_id, "e": event_id},
            ).first()
            if participant is None:
                raise ApiError(
                    "VALIDATION_ERROR",
                    details={"user_id": "Participant must have an active team membership in the selected event."},
                )
        s.execute(
            text(
                "INSERT INTO app.time_adjustments (id, stage_id, user_id, delta_seconds, reason_code, created_by) VALUES (CAST(:id AS uuid), CAST(:s AS uuid), CAST(:u AS uuid), :d, :r, CAST(:by AS uuid))"
            ),
            {"id": str(uuid7()), "s": stage_id, "u": user_id, "d": delta, "r": reason_code, "by": p.id},
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="stage.time_adjustment",
            entity_type="stage",
            entity_id=stage_id,
            request_id=request.state.request_id,
            details={"delta_seconds": delta, "reason_code": reason_code},
        )
    return {"ok": True}


@router.get("/admin/events/{event_id}/live")
def live_ops(event_id: str, p: Principal = Depends(require_organizer)):
    """Control-room view with REAL state (ADM-LV-01); no fabricated indicators."""
    with app_session() as s:
        counts = s.execute(
            text("SELECT status, count(*) FROM app.submissions WHERE event_id = CAST(:e AS uuid) GROUP BY status"),
            {"e": event_id},
        ).all()
        teams = s.execute(
            text("SELECT count(*) FROM app.teams WHERE event_id = CAST(:e AS uuid)"), {"e": event_id}
        ).scalar_one()
        participants = s.execute(
            text(
                "SELECT count(DISTINCT tm.user_id) FROM app.team_members tm JOIN app.teams t ON t.id = tm.team_id WHERE t.event_id = CAST(:e AS uuid)"
            ),
            {"e": event_id},
        ).scalar_one()
        queue_age = s.execute(
            text(
                "SELECT COALESCE(MIN(EXTRACT(epoch FROM (now() - created_at))), 0) FROM app.jobs WHERE status='queued'"
            ),
        ).scalar_one()
        dead = s.execute(text("SELECT count(*) FROM app.jobs WHERE status='dead'")).scalar_one()
        stages = (
            s.execute(
                text(
                    "SELECT id, name, status, starts_at, ends_at, config FROM app.stages WHERE event_id = CAST(:e AS uuid) ORDER BY ordinal"
                ),
                {"e": event_id},
            )
            .mappings()
            .all()
        )
        rev_backlog = s.execute(text("SELECT count(*) FROM app.review_items WHERE status='open'")).scalar_one()
        probes = health_probe()
    return {
        "teams": int(teams),
        "participants_online_estimate": int(
            participants
        ),  # participants with accounts; live presence needs polling stats
        "submission_counts": {r[0]: int(r[1]) for r in counts},
        "job_queue": {"oldest_queued_age_s": int(queue_age), "dead_letters": int(dead)},
        "review_backlog": int(rev_backlog),
        "stages": [dict(r) for r in stages],
        "health": probes,
        "kill_switches": {
            "runner_enabled": settings.runner_enabled,
            "ai_assessment_enabled": settings.ai_assessment_enabled,
            "telemetry_enabled": settings.telemetry_enabled,
        },
    }


# --- readiness, audit, exports -------------------------------------------------


@router.get("/admin/events/{event_id}/readiness")
def readiness(event_id: str, p: Principal = Depends(require_organizer)):
    with app_session() as s:
        rows = (
            s.execute(
                text(
                    "SELECT key, label, state, owner, checked_at FROM app.readiness_items WHERE event_id = CAST(:e AS uuid) ORDER BY key"
                ),
                {"e": event_id},
            )
            .mappings()
            .all()
        )
    return {"items": [dict(r) for r in rows]}


@router.post("/admin/events/{event_id}/readiness/{key}")
def set_readiness(event_id: str, key: str, body: dict, request: Request, p: Principal = Depends(require_organizer)):
    state = body.get("state", "checked")
    if state not in ("checked", "unchecked", "na"):
        raise ApiError("VALIDATION_ERROR")
    with app_session() as s:
        s.execute(
            text(
                "UPDATE app.readiness_items SET state=CAST(:st AS varchar), checked_at=CASE WHEN CAST(:st AS varchar)='checked' THEN now() ELSE NULL END WHERE event_id = CAST(:e AS uuid) AND key = :k"
            ),
            {"st": state, "e": event_id, "k": key},
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="readiness.updated",
            entity_type="event",
            entity_id=event_id,
            request_id=request.state.request_id,
            details={"key": key, "state": state},
        )
    return {"ok": True}


@router.get("/admin/audit/verify")
def audit_chain_verify(p: Principal = Depends(require_organizer)):
    with app_session() as s:
        result = audit_verify(s)
    return result


@router.get("/admin/audit")
def audit_log(p: Principal = Depends(require_organizer), limit: int = 100, action: str | None = None):
    with app_session() as s:
        if action:
            rows = (
                s.execute(
                    text(
                        "SELECT seq, ts, actor_role, action, entity_type, entity_id, request_id, details FROM audit.entries WHERE action = :a ORDER BY seq DESC LIMIT :n"
                    ),
                    {"a": action, "n": min(limit, 500)},
                )
                .mappings()
                .all()
            )
        else:
            rows = (
                s.execute(
                    text(
                        "SELECT seq, ts, actor_role, action, entity_type, entity_id, request_id, details FROM audit.entries ORDER BY seq DESC LIMIT :n"
                    ),
                    {"n": min(limit, 500)},
                )
                .mappings()
                .all()
            )
    return {"entries": [dict(r) for r in rows]}


@router.get("/admin/events/{event_id}/export/{kind}")
def export_csv(event_id: str, kind: str, p: Principal = Depends(require_organizer)):
    if kind not in ("results", "submissions", "ledger"):
        raise ApiError("VALIDATION_ERROR", details={"kind": "results|submissions|ledger"})
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    created_by = p.id
    with app_session() as s:
        if kind == "results":
            writer.writerow(["team_id", "team_name", "score"])
            rows = s.execute(
                text(
                    "SELECT t.id, t.name, COALESCE(SUM(l.points), 0) AS total FROM app.teams t "
                    "LEFT JOIN app.score_ledger_entries l ON l.team_id = t.id "
                    "WHERE t.event_id = CAST(:e AS uuid) GROUP BY t.id, t.name ORDER BY total DESC"
                ),
                {"e": event_id},
            ).all()
            for r in rows:
                writer.writerow([str(r[0]), r[1], float(r[2] or 0)])
        elif kind == "submissions":
            writer.writerow(
                [
                    "submission_id",
                    "kind",
                    "title",
                    "status",
                    "receipt_code",
                    "received_at",
                    "file_path",
                    "line_start",
                    "line_end",
                ]
            )
            rows = s.execute(
                text(
                    "SELECT id, kind, title, status, receipt_code, received_at, file_path, line_start, line_end FROM app.submissions WHERE event_id = CAST(:e AS uuid) ORDER BY received_at"
                ),
                {"e": event_id},
            ).all()
            for r in rows:
                writer.writerow([str(r[0]), r[1], r[2], r[3], r[4], r[5].isoformat(), r[6], r[7], r[8]])
        else:  # ledger
            writer.writerow(
                [
                    "entry_id",
                    "stage_id",
                    "team_id",
                    "component",
                    "kind",
                    "points",
                    "reason_code",
                    "submission_id",
                    "created_at",
                ]
            )
            rows = s.execute(
                text(
                    "SELECT id, stage_id, team_id, component, kind, points, reason_code, submission_id, created_at FROM app.score_ledger_entries WHERE event_id = CAST(:e AS uuid) ORDER BY created_at"
                ),
                {"e": event_id},
            ).all()
            for r in rows:
                writer.writerow(
                    [
                        str(r[0]),
                        str(r[1]),
                        str(r[2]),
                        r[3],
                        r[4],
                        float(r[5]),
                        r[6],
                        str(r[7]) if r[7] else "",
                        r[8].isoformat(),
                    ]
                )
    content = buf.getvalue()
    with app_session() as s:
        s.execute(
            text(
                "INSERT INTO app.exports (id, event_id, kind, content, content_hash, created_by) VALUES (CAST(:id AS uuid), CAST(:e AS uuid), :k, :c, :h, CAST(:u AS uuid))"
            ),
            {
                "id": str(uuid7()),
                "e": event_id,
                "k": f"{kind}_csv",
                "c": content,
                "h": __import__("hashlib").sha256(content.encode()).hexdigest(),
                "u": created_by,
            },
        )
    return {"filename": f"{kind}_{event_id[:8]}.csv", "content": content}


# --- disputes -------------------------------------------------------------------


@router.get("/admin/disputes")
def list_disputes(p: Principal = Depends(require_organizer)):
    with app_session() as s:
        rows = (
            s.execute(
                text(
                    "SELECT d.id, d.status, d.statement, d.created_at, s.title, s.receipt_code, d.submission_id "
                    "FROM app.disputes d JOIN app.submissions s ON s.id = d.submission_id ORDER BY d.created_at DESC"
                )
            )
            .mappings()
            .all()
        )
    return {"disputes": [dict(r) for r in rows]}


@router.post("/admin/disputes/{dispute_id}/resolve")
def resolve_dispute(dispute_id: str, body: dict, request: Request, p: Principal = Depends(require_organizer)):
    outcome = body.get("outcome")  # upheld | rejected
    resolution = body.get("resolution", "")
    adjust_points = body.get("adjust_points")  # optional ledger adjustment for the team
    if outcome not in ("upheld", "rejected"):
        raise ApiError("VALIDATION_ERROR", details={"outcome": "upheld|rejected"})
    with app_session() as s:
        d = (
            s.execute(
                text(
                    "SELECT di.id, di.submission_id, di.team_id, di.status, s.stage_id "
                    "FROM app.disputes di JOIN app.submissions s ON s.id = di.submission_id "
                    "WHERE di.id = CAST(:id AS uuid) FOR UPDATE"
                ),
                {"id": dispute_id},
            )
            .mappings()
            .first()
        )
        if d is None:
            raise ApiError("NOT_FOUND")
        if d["status"] in ("upheld", "rejected"):
            raise ApiError("VALIDATION_ERROR", details={"dispute": "Already resolved."})
        s.execute(
            text(
                "UPDATE app.disputes SET status=:o, resolution=:r, resolved_by=CAST(:u AS uuid), resolved_at=now() WHERE id = CAST(:id AS uuid)"
            ),
            {"o": outcome, "r": resolution, "u": p.id, "id": dispute_id},
        )
        if outcome == "upheld" and adjust_points:
            # rescoring via ledger adjustment (DAT-05): reversal + new entry
            stage_id = str(d["stage_id"])
            team_id = str(d["team_id"])
            event_id = s.execute(
                text("SELECT event_id FROM app.stages WHERE id = CAST(:s AS uuid)"), {"s": stage_id}
            ).scalar_one()
            tenant_id = s.execute(
                text("SELECT tenant_id FROM app.events WHERE id = CAST(:e AS uuid)"), {"e": str(event_id)}
            ).scalar_one()
            add_ledger_entry(
                s,
                tenant_id=str(tenant_id),
                event_id=str(event_id),
                stage_id=stage_id,
                team_id=team_id,
                component="adjustment",
                kind="adjustment",
                points=float(adjust_points),
                reason_code="DISPUTE_UPHELD",
                submission_id=str(d["submission_id"]),
                actor_id=p.id,
                note=f"Dispute {dispute_id[:8]} upheld: {resolution[:200]}",
            )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="dispute.resolved",
            entity_type="dispute",
            entity_id=dispute_id,
            request_id=request.state.request_id,
            details={"outcome": outcome},
        )
    return {"ok": True}


@router.post("/admin/adjustments")
def manual_adjustment(body: dict, request: Request, p: Principal = Depends(step_up_verified)):
    """Admin-only, step-up-protected manual score adjustment (audit reason required; ADM-RS)."""
    if p.role != "admin":
        raise ApiError("AUTH_FORBIDDEN")
    stage_id = body.get("stage_id")
    team_id = body.get("team_id")
    points = float(body.get("points", 0))
    reason = body.get("reason", "")
    if not stage_id or not team_id or not reason or points == 0:
        raise ApiError("VALIDATION_ERROR", details={"adjustment": "stage_id, team_id, points, reason required."})
    with app_session() as s:
        event_id = s.execute(
            text("SELECT event_id FROM app.stages WHERE id = CAST(:s AS uuid)"), {"s": stage_id}
        ).scalar_one()
        tenant_id = s.execute(
            text("SELECT tenant_id FROM app.events WHERE id = CAST(:e AS uuid)"), {"e": str(event_id)}
        ).scalar_one()
        team_event = s.execute(
            text("SELECT event_id FROM app.teams WHERE id = CAST(:t AS uuid)"), {"t": team_id}
        ).scalar_one_or_none()
        if str(team_event) != str(event_id):
            raise ApiError("VALIDATION_ERROR", details={"team_id": "Team must belong to the selected stage event."})
        add_ledger_entry(
            s,
            tenant_id=str(tenant_id),
            event_id=str(event_id),
            stage_id=stage_id,
            team_id=team_id,
            component="adjustment",
            kind="adjustment",
            points=points,
            reason_code="MANUAL",
            actor_id=p.id,
            note=reason,
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="scoring.manual_adjustment",
            entity_type="team",
            entity_id=team_id,
            request_id=request.state.request_id,
            details={"points": points, "reason": reason},
            event_scope="scoring",
        )
    return {"ok": True}


@router.get("/admin/system/health")
def system_health(p: Principal = Depends(require_organizer)):
    probes = health_probe()
    with app_session() as s:
        jobs = s.execute(text("SELECT status, count(*) FROM app.jobs GROUP BY status")).all()
        oldest = s.execute(
            text(
                "SELECT COALESCE(MIN(EXTRACT(epoch FROM (now() - created_at))), 0) FROM app.jobs WHERE status='queued'"
            )
        ).scalar_one()
    return {
        "databases": {"app": probes["database_app"], "gt": probes["database_gt"]},
        "jobs": {r[0]: int(r[1]) for r in jobs},
        "oldest_queued_job_age_s": int(oldest),
        "profile": settings.profile,
        "environment": settings.environment,
        "runner_enabled": settings.runner_enabled,
        "ai_assessment_enabled": settings.ai_assessment_enabled,
    }
