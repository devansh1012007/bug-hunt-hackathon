"""Regression tests for result release and canonical ledger totals."""

from __future__ import annotations

import uuid

from sqlalchemy import text

from conftest import participant_client


def test_evaluation_does_not_publish_results_or_live_leaderboard(client):
    """The evaluate step may prepare scores, but only explicit release exposes them."""
    from app.db import app_session
    from app.evaluator import release_feedback

    pc = participant_client(client, "p1.team1@example.com")
    stage_id = pc._stage_id

    with app_session() as s:
        event_id = s.execute(
            text("SELECT event_id FROM app.stages WHERE id = CAST(:s AS uuid)"),
            {"s": stage_id},
        ).scalar_one()
        s.execute(
            text(
                "UPDATE app.stages SET status='evaluating', "
                "config=jsonb_set(config, '{leaderboard_mode}', to_jsonb('live'::text), true), "
                "results_published_at=NULL WHERE id = CAST(:s AS uuid)"
            ),
            {"s": stage_id},
        )
        s.execute(
            text(
                "INSERT INTO app.leaderboard_snapshots "
                "(id, event_id, stage_id, version, payload, visibility) VALUES "
                "(CAST(:id AS uuid), CAST(:e AS uuid), CAST(:s AS uuid), 1, "
                "CAST(:payload AS jsonb), 'public')"
            ),
            {
                "id": str(uuid.uuid4()),
                "e": str(event_id),
                "s": stage_id,
                "payload": '{"stage_id":"redacted-test","event_id":"redacted-test","rows":[{"rank":1,"score":999}]}',
            },
        )

    release_feedback({"stage_id": stage_id, "publish": False})

    hidden_board = pc.get(f"/v1/leaderboard/{stage_id}")
    assert hidden_board.status_code == 200
    assert hidden_board.json()["visible"] is False

    hidden_results = pc.get(f"/v1/results/{stage_id}")
    assert hidden_results.status_code == 200
    assert hidden_results.json()["released"] is False

    # Simulate the organizer's explicit release transition, then run its job.
    with app_session() as s:
        s.execute(
            text("UPDATE app.stages SET status='released' WHERE id = CAST(:s AS uuid)"),
            {"s": stage_id},
        )
    release_feedback({"stage_id": stage_id, "publish": True})

    published = pc.get(f"/v1/results/{stage_id}")
    assert published.status_code == 200
    assert published.json()["released"] is True
    visible_board = pc.get(f"/v1/leaderboard/{stage_id}")
    assert visible_board.status_code == 200
    assert visible_board.json()["visible"] is True


def test_participant_total_counts_penalty_once(client):
    """A negative penalty row is already part of the ledger sum and is not subtracted twice."""
    from app.db import app_session

    pc = participant_client(client, "p1.team1@example.com")
    stage_id = pc._stage_id

    with app_session() as s:
        stage = (
            s.execute(
                text(
                    "SELECT st.event_id, e.tenant_id FROM app.stages st "
                    "JOIN app.events e ON e.id = st.event_id WHERE st.id = CAST(:s AS uuid)"
                ),
                {"s": stage_id},
            )
            .mappings()
            .one()
        )
        team_id = s.execute(
            text(
                "SELECT tm.team_id FROM app.team_members tm JOIN app.teams t ON t.id = tm.team_id "
                "WHERE tm.user_id = (SELECT id FROM app.users WHERE email = 'p1.team1@example.com') "
                "AND t.event_id = CAST(:e AS uuid) LIMIT 1"
            ),
            {"e": str(stage["event_id"])},
        ).scalar_one()
        for component, kind, points in (
            ("functional_test", "award", 100),
            ("penalty", "penalty", -20),
        ):
            s.execute(
                text(
                    "INSERT INTO app.score_ledger_entries "
                    "(id, tenant_id, event_id, stage_id, team_id, component, kind, points, reason_code) "
                    "VALUES (CAST(:id AS uuid), CAST(:tenant AS uuid), CAST(:event AS uuid), "
                    "CAST(:stage AS uuid), CAST(:team AS uuid), :component, :kind, :points, :reason)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant": str(stage["tenant_id"]),
                    "event": str(stage["event_id"]),
                    "stage": stage_id,
                    "team": str(team_id),
                    "component": component,
                    "kind": kind,
                    "points": points,
                    "reason": "TEST_RESULT_RELEASE",
                },
            )
        s.execute(
            text(
                "UPDATE app.stages SET status='released', results_published_at=now() "
                "WHERE id = CAST(:s AS uuid)"
            ),
            {"s": stage_id},
        )

    response = pc.get(f"/v1/results/{stage_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["released"] is True
    assert payload["total"] == 80.0
    assert payload["penalties"] == -20.0
    assert payload["by_component"]["penalty"] == -20.0
