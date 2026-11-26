"""Organizer setup APIs and privileged scoring controls."""

from __future__ import annotations

import uuid

from conftest import STAFF_PW, login
from fastapi.testclient import TestClient


def _staff_client(client, email: str) -> TestClient:
    auth = login(client, email, STAFF_PW)
    staff = TestClient(client.app)
    staff.headers.update({"Authorization": f"Bearer {auth['access_token']}"})
    return staff


def test_organizer_setup_apis_cover_stages_teams_invites_and_revisions(client):
    organizer = _staff_client(client, "organizer@example.com")
    event = organizer.get("/v1/admin/events").json()["events"][0]
    event_id = event["id"]

    stage_response = organizer.post(
        f"/v1/admin/events/{event_id}/stages",
        json={"name": "Organizer-created stage", "kind": "practice", "grace_seconds": 15, "config": {}},
    )
    assert stage_response.status_code == 201, stage_response.text
    stage_id = stage_response.json()["stage_id"]

    team_name = f"Organizer team {uuid.uuid4().hex[:8]}"
    team_response = organizer.post(f"/v1/admin/events/{event_id}/teams", json={"name": team_name})
    assert team_response.status_code == 201, team_response.text
    team_id = team_response.json()["team_id"]
    assert team_response.json()["join_code"]

    teams = organizer.get(f"/v1/admin/events/{event_id}/teams")
    assert teams.status_code == 200, teams.text
    listed_team = next(row for row in teams.json()["teams"] if row["id"] == team_id)
    assert listed_team["name"] == team_name
    assert listed_team["member_count"] == 0
    assert "join_code" not in listed_team

    invited_email = f"organizer-test-{uuid.uuid4().hex[:10]}@example.test"
    invite_response = organizer.post(
        f"/v1/admin/events/{event_id}/invites",
        json={"email": invited_email, "team_id": team_id, "role": "participant"},
    )
    assert invite_response.status_code == 201, invite_response.text
    invite = invite_response.json()
    assert invite["invite_token"]
    assert invite["activation_url"].startswith("/activate?invite=")

    roster = organizer.get(f"/v1/admin/events/{event_id}/participants")
    assert roster.status_code == 200, roster.text
    participant = next(row for row in roster.json()["participants"] if row["email"] == invited_email)
    assert participant["id"] is None
    assert participant["status"] == "invited"
    assert participant["activated"] is False
    assert participant["team_id"] == team_id

    challenges = organizer.get(f"/v1/admin/events/{event_id}/challenges")
    assert challenges.status_code == 200, challenges.text
    assert challenges.json()["challenges"]
    challenge = challenges.json()["challenges"][0]
    assert {"id", "slug", "title", "version", "status"} <= set(challenge)
    assert "instructions_md" not in challenge
    assert "flag_code" not in challenge

    config = organizer.get(f"/v1/admin/events/{event_id}/config/revisions")
    assert config.status_code == 200, config.text
    config_response = organizer.post(
        f"/v1/admin/events/{event_id}/config",
        json={
            "config": config.json()["current_config"],
            "reason": "Verify organizer configuration revision workflow",
            "freeze": True,
        },
    )
    assert config_response.status_code == 200, config_response.text

    revisions = organizer.get(f"/v1/admin/events/{event_id}/config/revisions")
    assert revisions.status_code == 200, revisions.text
    assert revisions.json()["config_version"] == event["config_version"] + 1
    assert revisions.json()["revisions"][0]["frozen_at"] is not None
    assert revisions.json()["revisions"][0]["reason"] == "Verify organizer configuration revision workflow"

    time_response = organizer.post(
        f"/v1/admin/events/{event_id}/time-adjustment",
        json={
            "stage_id": stage_id,
            "delta_seconds": 120,
            "reason_code": "OUTAGE",
            "user_id": None,
        },
    )
    assert time_response.status_code == 200, time_response.text

    wrong_event = organizer.post(
        f"/v1/admin/events/{uuid.uuid4()}/time-adjustment",
        json={
            "stage_id": stage_id,
            "delta_seconds": 120,
            "reason_code": "OUTAGE",
            "user_id": None,
        },
    )
    assert wrong_event.status_code == 422, wrong_event.text
    assert wrong_event.json()["error"]["code"] == "VALIDATION_ERROR"

    wrong_participant = organizer.post(
        f"/v1/admin/events/{event_id}/time-adjustment",
        json={
            "stage_id": stage_id,
            "delta_seconds": 120,
            "reason_code": "OUTAGE",
            "user_id": str(uuid.uuid4()),
        },
    )
    assert wrong_participant.status_code == 422, wrong_participant.text
    assert wrong_participant.json()["error"]["code"] == "VALIDATION_ERROR"


def test_new_admin_read_apis_reject_participants(client):
    from conftest import participant_client

    participant = participant_client(client, "p1.team1@example.com")
    event_id = participant.get("/v1/me").json()["team"]["event_id"]

    for path in (
        f"/v1/admin/events/{event_id}/teams",
        f"/v1/admin/events/{event_id}/participants",
        f"/v1/admin/events/{event_id}/challenges",
        f"/v1/admin/events/{event_id}/config/revisions",
    ):
        response = participant.get(path)
        assert response.status_code == 403, f"{path}: {response.status_code} {response.text}"


def test_manual_score_adjustment_requires_admin_and_step_up(client):
    organizer = _staff_client(client, "organizer@example.com")
    event = organizer.get("/v1/admin/events").json()["events"][0]
    event_id = event["id"]
    stage_id = organizer.get(f"/v1/admin/events/{event_id}/live").json()["stages"][0]["id"]
    team_id = organizer.get(f"/v1/admin/events/{event_id}/teams").json()["teams"][0]["id"]
    payload = {"stage_id": stage_id, "team_id": team_id, "points": 3.5, "reason": "Test audited correction"}

    denied = organizer.post("/v1/admin/adjustments", json=payload)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ADMIN_STEP_UP_REQUIRED"

    organizer.post("/v1/auth/step-up", json={"password": STAFF_PW}).raise_for_status()
    still_denied = organizer.post("/v1/admin/adjustments", json=payload)
    assert still_denied.status_code == 403
    assert still_denied.json()["error"]["code"] == "AUTH_FORBIDDEN"

    admin = _staff_client(client, "admin@example.com")
    admin.post("/v1/auth/step-up", json={"password": STAFF_PW}).raise_for_status()
    accepted = admin.post("/v1/admin/adjustments", json=payload)
    assert accepted.status_code == 200, accepted.text
