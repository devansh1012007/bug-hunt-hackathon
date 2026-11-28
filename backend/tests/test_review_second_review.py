"""Regression tests for independent second-review workflow."""

from __future__ import annotations

import uuid

from conftest import STAFF_PW, login, participant_client
from sqlalchemy import text


def test_needs_second_review_creates_independent_review_task(client):
    """A second-review request creates real work and forbids the first reviewer from self-review."""
    from app.db import app_session
    from fastapi.testclient import TestClient

    participant = participant_client(client, "p1.team1@example.com")
    stage_id = participant._stage_id
    submission_id = str(uuid.uuid4())
    first_item_id = str(uuid.uuid4())

    with app_session() as s:
        context = (
            s.execute(
                text(
                    "SELECT u.id AS author_id, tm.team_id, t.event_id, e.tenant_id, cv.id AS challenge_version_id "
                    "FROM app.users u "
                    "JOIN app.team_members tm ON tm.user_id = u.id "
                    "JOIN app.teams t ON t.id = tm.team_id "
                    "JOIN app.events e ON e.id = t.event_id "
                    "JOIN app.challenge_versions cv ON cv.event_id = e.id "
                    "WHERE u.email = 'p1.team1@example.com' LIMIT 1"
                )
            )
            .mappings()
            .one()
        )
        s.execute(
            text(
                "INSERT INTO app.submissions "
                "(id, tenant_id, event_id, stage_id, challenge_version_id, team_id, author_id, "
                "kind, idempotency_key, payload, payload_hash, status, receipt_code, title, received_at) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant AS uuid), CAST(:event AS uuid), "
                "CAST(:stage AS uuid), CAST(:challenge AS uuid), CAST(:team AS uuid), CAST(:author AS uuid), "
                "'bug_report', :key, '{}'::jsonb, :hash, 'needs_review', :receipt, 'Second review fixture', now())"
            ),
            {
                "id": submission_id,
                "tenant": str(context["tenant_id"]),
                "event": str(context["event_id"]),
                "stage": stage_id,
                "challenge": str(context["challenge_version_id"]),
                "team": str(context["team_id"]),
                "author": str(context["author_id"]),
                "key": f"second-review-{uuid.uuid4().hex[:12]}",
                "hash": "a" * 64,
                "receipt": uuid.uuid4().hex[:16].upper(),
            },
        )
        s.execute(
            text(
                "INSERT INTO app.review_items (id, submission_id, status, priority, reason) "
                "VALUES (CAST(:id AS uuid), CAST(:submission AS uuid), 'open', 5, 'manual_review')"
            ),
            {"id": first_item_id, "submission": submission_id},
        )

    first_token = login(client, "reviewer1@example.com", STAFF_PW)["access_token"]
    first_reviewer = TestClient(client.app)
    first_reviewer.headers.update({"Authorization": f"Bearer {first_token}"})

    claimed = first_reviewer.post(f"/v1/review/items/{first_item_id}/claim")
    assert claimed.status_code == 200, claimed.text
    decision = first_reviewer.post(
        f"/v1/review/items/{first_item_id}/decision",
        json={"decision": "needs_second_review", "rubric": {}, "notes": "Independent confirmation required."},
    )
    assert decision.status_code == 200, decision.text

    with app_session() as s:
        rows = (
            s.execute(
                text(
                    "SELECT id, status, reason FROM app.review_items "
                    "WHERE submission_id = CAST(:submission AS uuid) ORDER BY created_at, id"
                ),
                {"submission": submission_id},
            )
            .mappings()
            .all()
        )
        submission_status = s.execute(
            text("SELECT status FROM app.submissions WHERE id = CAST(:id AS uuid)"),
            {"id": submission_id},
        ).scalar_one()

    assert len(rows) == 2
    assert rows[0]["status"] == "decided"
    assert rows[1]["status"] == "open"
    assert rows[1]["reason"] == "needs_second_review"
    assert submission_status == "in_review"
    second_item_id = str(rows[1]["id"])

    self_claim = first_reviewer.post(f"/v1/review/items/{second_item_id}/claim")
    assert self_claim.status_code == 403
    assert self_claim.json()["error"]["code"] == "AUTH_FORBIDDEN"

    self_decision = first_reviewer.post(
        f"/v1/review/items/{second_item_id}/decision",
        json={"decision": "accept_full", "rubric": {}},
    )
    assert self_decision.status_code == 403
    assert self_decision.json()["error"]["code"] == "AUTH_FORBIDDEN"

    second_token = login(client, "reviewer2@example.com", STAFF_PW)["access_token"]
    second_reviewer = TestClient(client.app)
    second_reviewer.headers.update({"Authorization": f"Bearer {second_token}"})
    second_claim = second_reviewer.post(f"/v1/review/items/{second_item_id}/claim")
    assert second_claim.status_code == 200, second_claim.text

    second_decision = second_reviewer.post(
        f"/v1/review/items/{second_item_id}/decision",
        json={"decision": "accept_full", "rubric": {}, "notes": "Independent second review complete."},
    )
    assert second_decision.status_code == 200, second_decision.text

    with app_session() as s:
        final_status = s.execute(
            text("SELECT status FROM app.submissions WHERE id = CAST(:id AS uuid)"),
            {"id": submission_id},
        ).scalar_one()
        open_items = s.execute(
            text(
                "SELECT count(*) FROM app.review_items WHERE submission_id = CAST(:id AS uuid) AND status <> 'decided'"
            ),
            {"id": submission_id},
        ).scalar_one()

    assert final_status == "decided"
    assert open_items == 0
