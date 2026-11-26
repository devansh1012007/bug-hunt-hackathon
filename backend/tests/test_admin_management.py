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


def test_organizer_setup_and_admin_participant_lifecycle(client):
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

    second_team_response = organizer.post(
        f"/v1/admin/events/{event_id}/teams",
        json={"name": f"Second team {uuid.uuid4().hex[:8]}"},
    )
    assert second_team_response.status_code == 201, second_team_response.text
    second_team_id = second_team_response.json()["team_id"]

    teams = organizer.get(f"/v1/admin/events/{event_id}/teams")
    assert teams.status_code == 200, teams.text
    listed_team = next(row for row in teams.json()["teams"] if row["id"] == team_id)
    assert listed_team["name"] == team_name
    assert listed_team["member_count"] == 0
    assert "join_code" not in listed_team

    # Organizers may inspect the roster, but participant lifecycle mutations are admin-only.
    organizer.post("/v1/auth/step-up", json={"password": STAFF_PW}).raise_for_status()
    denied = organizer.post(
        f"/v1/admin/events/{event_id}/participants",
        json={
            "email": f"not-admin-{uuid.uuid4().hex[:8]}@example.test",
            "name": "Not an Admin",
            "team_id": team_id,
        },
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "AUTH_FORBIDDEN"

    admin = _staff_client(client, "admin@example.com")
    admin.post("/v1/auth/step-up", json={"password": STAFF_PW}).raise_for_status()

    participant_email = f"registered-{uuid.uuid4().hex[:10]}@example.test"
    registration = admin.post(
        f"/v1/admin/events/{event_id}/participants",
        json={"email": participant_email, "name": "Registered Participant", "team_id": team_id},
    )
    assert registration.status_code == 201, registration.text
    registered = registration.json()
    participant_id = registered["participant_id"]
    assert registered["password_setup_required"] is True
    assert "invite_token" not in registered
    assert "activation_url" not in registered

    roster = organizer.get(f"/v1/admin/events/{event_id}/participants")
    assert roster.status_code == 200, roster.text
    event_roster = roster.json()["participants"]
    seeded_member = next(row for row in event_roster if row["email"] == "p1.team1@example.com")
    assert seeded_member["team_name"] == "Team Lambda"
    assert seeded_member["password_set"] is True
    participant = next(row for row in event_roster if row["email"] == participant_email)
    assert participant["id"] == participant_id
    assert participant["status"] == "active"
    assert participant["team_id"] == team_id
    assert participant["password_set"] is False
    assert "invite_token" not in participant
    assert "expires_at" not in participant

    first_password = "First-SignIn-Pass-123"
    first_login = client.post(
        "/v1/auth/login",
        json={"email": participant_email, "password": first_password},
    )
    assert first_login.status_code == 200, first_login.text
    participant_headers = {"Authorization": f"Bearer {first_login.json()['access_token']}"}
    assert client.get("/v1/me", headers=participant_headers).status_code == 200

    wrong_password = client.post(
        "/v1/auth/login",
        json={"email": participant_email, "password": "Different-Password-123"},
    )
    assert wrong_password.status_code == 401
    assert wrong_password.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"

    roster = organizer.get(f"/v1/admin/events/{event_id}/participants")
    participant = next(row for row in roster.json()["participants"] if row["id"] == participant_id)
    assert participant["password_set"] is True

    moved = admin.patch(
        f"/v1/admin/events/{event_id}/participants/{participant_id}/team",
        json={"team_id": second_team_id},
    )
    assert moved.status_code == 200, moved.text
    roster = organizer.get(f"/v1/admin/events/{event_id}/participants")
    participant = next(row for row in roster.json()["participants"] if row["id"] == participant_id)
    assert participant["team_id"] == second_team_id

    reset_password = "Admin-Reset-Password-123"
    reset = admin.post(
        f"/v1/admin/users/{participant_id}/password-reset",
        json={"password": reset_password},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["email"] == participant_email
    assert client.get("/v1/me", headers=participant_headers).status_code == 401
    assert client.post(
        "/v1/auth/login",
        json={"email": participant_email, "password": first_password},
    ).status_code == 401
    new_login = client.post(
        "/v1/auth/login",
        json={"email": participant_email, "password": reset_password},
    )
    assert new_login.status_code == 200, new_login.text

    removed = admin.delete(f"/v1/admin/events/{event_id}/participants/{participant_id}")
    assert removed.status_code == 200, removed.text
    disabled_login = client.post(
        "/v1/auth/login",
        json={"email": participant_email, "password": reset_password},
    )
    assert disabled_login.status_code == 403
    assert disabled_login.json()["error"]["code"] == "AUTH_NOT_ACTIVATED"

    # The reset API also refuses an organizer even after their step-up check.
    forbidden_reset = organizer.post(
        f"/v1/admin/users/{participant_id}/password-reset",
        json={"password": "Another-Reset-Password-123"},
    )
    assert forbidden_reset.status_code == 403
    assert forbidden_reset.json()["error"]["code"] == "AUTH_FORBIDDEN"

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
