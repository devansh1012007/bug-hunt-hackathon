"""Regression tests for result release and canonical ledger totals."""

from __future__ import annotations

import uuid

from conftest import login, participant_client, worker_drain
from sqlalchemy import text


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
        team = (
            s.execute(
                text(
                    "SELECT tm.team_id, e.tenant_id FROM app.team_members tm "
                    "JOIN app.teams t ON t.id = tm.team_id JOIN app.events e ON e.id = t.event_id "
                    "WHERE tm.user_id = (SELECT id FROM app.users WHERE email = 'p1.team1@example.com') "
                    "AND e.id = CAST(:e AS uuid) LIMIT 1"
                ),
                {"e": str(event_id)},
            )
            .mappings()
            .one()
        )
        hidden_stage_id = str(uuid.uuid4())
        s.execute(
            text(
                "INSERT INTO app.stages (id, event_id, name, kind, ordinal, status, config) "
                "VALUES (CAST(:id AS uuid), CAST(:e AS uuid), 'Hidden score fixture', 'level', 99, "
                "'ended', CAST(:config AS jsonb))"
            ),
            {
                "id": hidden_stage_id,
                "e": str(event_id),
                "config": '{"max_points":1000}',
            },
        )
        s.execute(
            text(
                "INSERT INTO app.score_ledger_entries "
                "(id, tenant_id, event_id, stage_id, team_id, component, kind, points, reason_code) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant AS uuid), CAST(:event AS uuid), "
                "CAST(:stage AS uuid), CAST(:team AS uuid), 'functional_test', 'award', 999, 'HIDDEN_FIXTURE')"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant": str(team["tenant_id"]),
                "event": str(event_id),
                "stage": hidden_stage_id,
                "team": str(team["team_id"]),
            },
        )

    release_feedback({"stage_id": stage_id, "publish": False})

    hidden_board = pc.get(f"/v1/leaderboard/{stage_id}")
    assert hidden_board.status_code == 200
    assert hidden_board.json()["visible"] is False

    hidden_results = pc.get(f"/v1/results/{stage_id}")
    assert hidden_results.status_code == 200
    assert hidden_results.json()["released"] is False

    # The separate dashboard endpoint must not expose raw score components.
    hidden_team_score = pc.get("/v1/team/score")
    assert hidden_team_score.status_code == 200
    hidden_level = next(level for level in hidden_team_score.json()["levels"] if level["level_no"] == 99)
    assert hidden_level["evaluation_status"] == "not_released"
    assert hidden_level["components"] == {}
    assert hidden_level["score"] == 0.0

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
    # The pre-publication public snapshot must not be mistaken for a final
    # result. Publication is visible before the new snapshot job completes.
    assert visible_board.json()["snapshot"] is None

    worker_drain()
    refreshed_board = pc.get(f"/v1/leaderboard/{stage_id}")
    assert refreshed_board.status_code == 200
    assert refreshed_board.json()["visible"] is True
    assert refreshed_board.json()["snapshot"] is not None


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
            text("UPDATE app.stages SET status='released', results_published_at=now() WHERE id = CAST(:s AS uuid)"),
            {"s": stage_id},
        )

    response = pc.get(f"/v1/results/{stage_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["released"] is True
    assert payload["total"] == 80.0
    assert payload["penalties"] == -20.0
    assert payload["by_component"]["penalty"] == -20.0


def test_participant_cannot_read_another_events_published_leaderboard(client):
    """A leaked/guessed stage UUID must not bypass event membership."""
    from app.db import app_session

    pc = participant_client(client, "p1.team1@example.com")
    other_event_id = str(uuid.uuid4())
    other_stage_id = str(uuid.uuid4())

    with app_session() as s:
        tenant_id = s.execute(text("SELECT tenant_id FROM app.users WHERE email = 'p1.team1@example.com'")).scalar_one()
        s.execute(
            text(
                "INSERT INTO app.events (id, tenant_id, slug, name, timezone, status) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant AS uuid), :slug, 'Isolated event', "
                "'Asia/Kolkata', 'live')"
            ),
            {"id": other_event_id, "tenant": str(tenant_id), "slug": f"isolated-{uuid.uuid4().hex[:12]}"},
        )
        s.execute(
            text(
                "INSERT INTO app.stages (id, event_id, name, kind, ordinal, status, config, results_published_at) "
                "VALUES (CAST(:id AS uuid), CAST(:event AS uuid), 'Published stage', 'main', 1, "
                "'released', CAST(:config AS jsonb), now())"
            ),
            {
                "id": other_stage_id,
                "event": other_event_id,
                "config": '{"leaderboard_mode":"live"}',
            },
        )

    response = pc.get(f"/v1/leaderboard/{other_stage_id}")
    assert response.status_code == 404

def test_release_waits_for_pending_scoring_work_and_retries_cleanly(client):
    """Queued work blocks both the release API and its asynchronous publisher."""
    from fastapi.testclient import TestClient

    from app.db import app_session
    from app.evaluator import release_feedback
    from app.jobs import enqueue

    pc = participant_client(client, "p1.team1@example.com")
    stage_id = pc._stage_id
    token = login(client, "organizer@example.com")["access_token"]
    organizer = TestClient(client.app)
    organizer.headers.update({"Authorization": f"Bearer {token}"})

    with app_session() as s:
        s.execute(
            text(
                "UPDATE app.stages SET status='released', results_published_at=NULL "
                "WHERE id = CAST(:s AS uuid)"
            ),
            {"s": stage_id},
        )
        pending_job_id = enqueue(s, "release_feedback", {"stage_id": stage_id, "publish": False})

    # Defense in depth: even if a stale deployment already changed stage
    # status, the worker must not publish while the earlier scoring pass exists.
    try:
        release_feedback({"stage_id": stage_id, "publish": True})
    except RuntimeError as exc:
        assert "unfinished work" in str(exc)
    else:
        raise AssertionError("release worker published while a prerequisite job was queued")

    blocked = organizer.post(f"/v1/admin/stages/{stage_id}/release", json={"action": "release"})
    assert blocked.status_code == 422, blocked.text
    blocker_details = blocked.json()["error"]["details"]["release_blocked"]
    assert blocker_details["pending_scoring_jobs"] == 1

    with app_session() as s:
        s.execute(
            text("UPDATE app.jobs SET status='done' WHERE id = CAST(:id AS uuid)"),
            {"id": pending_job_id},
        )

    accepted = organizer.post(f"/v1/admin/stages/{stage_id}/release", json={"action": "release"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["release_pending"] is True

    with app_session() as s:
        before_worker = s.execute(
            text("SELECT status, results_published_at FROM app.stages WHERE id = CAST(:s AS uuid)"),
            {"s": stage_id},
        ).one()
        assert before_worker[0] == "released"
        assert before_worker[1] is None

    worker_drain()

    with app_session() as s:
        after_worker = s.execute(
            text("SELECT status, results_published_at FROM app.stages WHERE id = CAST(:s AS uuid)"),
            {"s": stage_id},
        ).one()
        assert after_worker[0] == "released"
        assert after_worker[1] is not None


