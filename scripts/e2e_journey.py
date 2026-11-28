#!/usr/bin/env python3
"""End-to-end journey verification against a RUNNING platform (API + worker).

Exercises the P0 journeys from ONE_SHOT_ACCEPTANCE_CRITERIA.md:
organizer lifecycle, participant activation->lobby->report submission with
idempotency, reviewer decision, scoring ledger, CTF flags, release, results,
dispute. Prints PASS/FAIL per step and exits nonzero on any failure.

Usage: python scripts/e2e_journey.py [--base http://127.0.0.1:8100]
Run scripts/run_dev.sh first.
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8100"
PARTICIPANT_PW = "Dev-only!Passw0rd!"
STAFF_PW = "Dev-only!Passw0rd!"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}: {name}" + (f" ({detail})" if detail and not ok else ""))


def login(client: httpx.Client, email: str, password: str) -> str:
    r = client.post(f"{BASE}/v1/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


def drain_jobs(client: httpx.Client, seconds: float = 10.0) -> None:
    """The worker polls every 2s; wait for it to catch up."""
    time.sleep(seconds)


def main() -> int:
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    BASE = args.base

    c = httpx.Client(timeout=15)
    print("== journey start ==")

    # --- 0. health -----------------------------------------------------------
    h = c.get(f"{BASE}/health").json()
    check("health: app db up", h.get("database_app") == "up", str(h))
    check("health: gt db up", h.get("database_gt") == "up", str(h))

    # --- 1. organizer: stage lifecycle ---------------------------------------
    org_token = login(c, "organizer@example.com", STAFF_PW)
    oc = httpx.Client(timeout=15, headers={"Authorization": f"Bearer {org_token}"})
    events = oc.get(f"{BASE}/v1/admin/events").json()["events"]
    event_id = events[0]["id"]
    # event lifecycle: configured -> ready (readiness gate) -> live
    r = oc.post(f"{BASE}/v1/admin/events/{event_id}/status", json={"status": "ready"})
    check("event configured -> ready", r.status_code == 200, r.text[:200])
    for item in oc.get(f"{BASE}/v1/admin/events/{event_id}/readiness").json()["items"]:
        oc.post(f"{BASE}/v1/admin/events/{event_id}/readiness/{item['key']}", json={"state": "checked"})
    r = oc.post(f"{BASE}/v1/admin/events/{event_id}/status", json={"status": "live"})
    check("readiness gate blocks/permits live transition", r.status_code == 200, r.text[:200])
    # stage id from a staff state read (staff see configured+ stages too)
    stages = oc.get(f"{BASE}/v1/admin/events/{event_id}/live").json()["stages"]
    stage_id = stages[0]["id"]
    r = oc.post(f"{BASE}/v1/admin/stages/{stage_id}/control", json={"action": "seal", "reason": "e2e rehearsal"})
    check("organizer seals the stage", r.status_code == 200, r.text[:200])
    r = oc.post(f"{BASE}/v1/admin/stages/{stage_id}/control", json={"action": "arm", "reason": "e2e rehearsal"})
    check("organizer arms the stage", r.status_code == 200, r.text[:200])
    r = oc.post(f"{BASE}/v1/admin/stages/{stage_id}/control", json={"action": "start", "reason": "e2e rehearsal"})
    check("organizer starts the stage", r.status_code == 200, r.text[:200])

    # --- 2. participant: login, rules, lobby ---------------------------------
    p_token = login(c, "p1.team1@example.com", PARTICIPANT_PW)
    pc = httpx.Client(timeout=15, headers={"Authorization": f"Bearer {p_token}"})
    me = pc.get(f"{BASE}/v1/me").json()
    check("participant login + profile", me.get("role") == "participant", str(me)[:200])
    check("participant has a team", (me.get("team") or {}).get("name") == "Team Lambda", str(me.get("team")))
    if not me.get("rules_accepted"):
        pc.post(f"{BASE}/v1/me/acceptances", json={}).raise_for_status()
    me = pc.get(f"{BASE}/v1/me").json()
    check("rules acceptance recorded", me.get("rules_accepted") is True)

    st = pc.get(f"{BASE}/v1/state").json()
    check("round state shows active stage", st["round"]["status"] == "active", str(st["round"])[:150])
    t = pc.get(f"{BASE}/v1/time").json()
    check("server time endpoint", "server_time_ms" in t)

    # --- 3. challenge files ---------------------------------------------------
    asg = pc.get(f"{BASE}/v1/assignments").json()
    check("assignment visible", len(asg["assignments"]) == 1, str(asg)[:200])
    challenge = asg["assignments"][0]
    check("challenge files listed", len(challenge["files"]) >= 11, str(len(challenge.get("files", []))))
    check("instructions present", "Papyrus" in (challenge.get("instructions_markdown") or ""))
    fid = challenge["challenge_id"]
    content = pc.get(f"{BASE}/v1/challenges/{fid}/files/content", params={"path": "routes/notes.js"}).json()
    check("file content readable", "deleteNote" in content.get("content", ""))
    r = pc.get(f"{BASE}/v1/challenges/{fid}/files/content", params={"path": "../../etc/passwd"})
    check("path traversal rejected", r.status_code == 400, f"{r.status_code}")
    r = pc.get(f"{BASE}/v1/challenges/{fid}/files/content", params={"path": "ORGANIZER_ANSWER_KEY.md"})
    check("answer key not served", r.status_code == 404, f"{r.status_code}")

    # --- 4. submit a bug report (idempotent) ----------------------------------
    idem = str(uuid.uuid4())
    report = {
        "kind": "bug_report",
        "challenge_id": fid,
        "file_path": "routes/notes.js",
        "line_start": 44,
        "line_end": 46,
        "category": "data_handling",
        "title": "Delete uses author id as note id",
        "description": "The delete route passes note.author_id into a query that matches by note id, so the wrong note is deleted silently when ids differ.",
        "impact": "Silent loss of user data",
        "evidence": "DELETE /api/notes/2 removes note 1 for user id 1; verified locally.",
        "suggested_fix": "Pass note.id instead of note.author_id.",
    }
    r1 = pc.post(f"{BASE}/v1/submissions", json=report, headers={"Idempotency-Key": idem})
    check("report accepted (202)", r1.status_code == 202, r1.text[:300])
    receipt1 = r1.json()
    check("receipt code present", bool(receipt1.get("receipt_code")), str(receipt1)[:200])
    r2 = pc.post(f"{BASE}/v1/submissions", json=report, headers={"Idempotency-Key": idem})
    check(
        "retry same key returns same receipt",
        r2.status_code == 202 and r2.json()["submission_id"] == receipt1["submission_id"],
        r2.text[:200],
    )
    r3 = pc.post(
        f"{BASE}/v1/submissions",
        json={**report, "title": "Different payload same key"},
        headers={"Idempotency-Key": idem},
    )
    check("same key different payload -> 409", r3.status_code == 409, f"{r3.status_code} {r3.text[:150]}")

    # cooldown: immediate second report on the same file must be 429
    r4 = pc.post(
        f"{BASE}/v1/submissions",
        json={**report, "title": "Cooldown probe second report", "line_start": 20, "line_end": 24},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    check(
        "file cooldown enforced",
        r4.status_code == 429 and r4.json()["error"]["code"] == "SUBMISSION_COOLDOWN",
        f"{r4.status_code} {r4.text[:150]}",
    )

    # second report on a different file
    time.sleep(1)
    report2 = {
        **report,
        "file_path": "db.js",
        "line_start": 57,
        "line_end": 60,
        "title": "Delete query binds wrong column",
        "description": "deleteNote prepares DELETE FROM notes WHERE id = ? and the route feeds it the author id; the wrong row disappears.",
        "category": "data_handling",
    }
    r5 = pc.post(f"{BASE}/v1/submissions", json=report2, headers={"Idempotency-Key": str(uuid.uuid4())})
    check("second report on another file accepted", r5.status_code == 202, r5.text[:200])
    receipt5 = r5.json()

    # IDOR: participant 2 cannot read participant 1's submission
    p2_token = login(c, "p1.team2@example.com", PARTICIPANT_PW)
    p2c = httpx.Client(timeout=15, headers={"Authorization": f"Bearer {p2_token}"})
    p2c.post(f"{BASE}/v1/me/acceptances", json={})
    r = p2c.get(f"{BASE}/v1/submissions/{receipt1['submission_id']}")
    check("IDOR: other participant's submission hidden", r.status_code == 404, f"{r.status_code}")

    # --- 5. evaluation pipeline ----------------------------------------------
    drain_jobs(c)
    subs = pc.get(f"{BASE}/v1/submissions").json()["submissions"]
    mine = [s for s in subs if s["id"] == receipt1["submission_id"]]
    # INT-01: participants see only "received" until results are released
    check(
        "feedback hidden before release (participant sees received only)",
        bool(mine) and mine[0]["status"] == "received",
        str(mine)[:250],
    )

    # --- 6. reviewer flow ------------------------------------------------------
    rev_token = login(c, "reviewer1@example.com", STAFF_PW)
    rc = httpx.Client(timeout=15, headers={"Authorization": f"Bearer {rev_token}"})
    # Both reports can produce review work. Release must not proceed while
    # either submission has an unresolved review item.
    expected_review_submissions = {receipt1["submission_id"], receipt5["submission_id"]}
    deadline = time.time() + 15
    queue = []
    review_items = []
    while time.time() < deadline:
        queue = rc.get(f"{BASE}/v1/review/queue").json()["queue"]
        review_items = [q for q in queue if q["submission_id"] in expected_review_submissions]
        if any(q["submission_id"] == receipt1["submission_id"] for q in review_items):
            break
        time.sleep(1.5)
    check(
        "evaluation pipeline created review work",
        any(q["submission_id"] == receipt1["submission_id"] for q in review_items),
        str(len(review_items)),
    )
    decisions = []
    for item in review_items:
        claimed = rc.post(f"{BASE}/v1/review/items/{item['review_item_id']}/claim")
        if claimed.status_code == 404:
            continue  # another queue refresh removed a stale item
        if claimed.status_code != 200:
            decisions.append((item["review_item_id"], claimed.status_code, claimed.text[:160]))
            continue
        response = rc.post(
            f"{BASE}/v1/review/items/{item['review_item_id']}/decision",
            json={
                "decision": "accept_full",
                "rubric": {
                    "functionality": 1.0,
                    "root_cause": 1.0,
                    "code_quality": 0.8,
                    "testing": 0.5,
                    "evidence": 1.0,
                },
                "notes": "verified against local repro",
            },
        )
        decisions.append((item["review_item_id"], response.status_code, response.text[:160]))
    check(
        "all event-journey review items resolved",
        all(status == 200 for _, status, _ in decisions),
        str(decisions),
    )

    # --- 7. organizer records functional test results (step-up) ---------------
    r = oc.post(f"{BASE}/v1/auth/step-up", json={"password": STAFF_PW})
    check("organizer step-up ok", r.status_code == 200, r.text[:150])
    r = oc.post(
        f"{BASE}/v1/admin/challenges/{fid}/ground-truth/test-results",
        json={"submission_id": receipt1["submission_id"], "tests_passed": 1, "tests_total": 1},
    )
    check("functional test results recorded", r.status_code == 200, r.text[:200])
    drain_jobs(c, 2.5)

    # --- 8. CTF flags -----------------------------------------------------------
    flag_wrong = {"kind": "ctf_flag", "flag_code": "FLAG{not_a_flag}", "title": "flag attempt"}
    r = pc.post(f"{BASE}/v1/submissions", json=flag_wrong, headers={"Idempotency-Key": str(uuid.uuid4())})
    check("wrong flag accepted as submission", r.status_code == 202, r.text[:150])
    flag_ok = {"kind": "ctf_flag", "flag_code": "FLAG{client_gates_1e_0nly_9f2a}", "title": "admin gate bypass flag"}
    r = pc.post(f"{BASE}/v1/submissions", json=flag_ok, headers={"Idempotency-Key": str(uuid.uuid4())})
    check("correct flag submitted", r.status_code == 202, r.text[:150])
    flag_receipt = r.json()
    drain_jobs(c, 4.0)
    # flag verification is hidden until release (INT-01); verified bonus is
    # asserted in the results breakdown after release (see ctf_bonus check)
    subs = pc.get(f"{BASE}/v1/submissions").json()["submissions"]
    flag_sub = next((s for s in subs if s["id"] == flag_receipt["submission_id"]), {})
    check(
        "flag status hidden from participant before release", flag_sub.get("status") == "received", str(flag_sub)[:150]
    )

    # --- 9. release results ------------------------------------------------------
    r = oc.post(f"{BASE}/v1/admin/stages/{stage_id}/control", json={"action": "end", "reason": "e2e rehearsal end"})
    check("stage ended", r.status_code == 200, r.text[:150])
    r = oc.post(f"{BASE}/v1/admin/stages/{stage_id}/release", json={"action": "evaluate", "reason": "e2e"})
    check("stage evaluating", r.status_code == 200, r.text[:150])
    drain_jobs(c, 3.0)
    r = oc.post(f"{BASE}/v1/admin/stages/{stage_id}/release", json={"action": "release", "reason": "e2e"})
    check("results released", r.status_code == 200, r.text[:150])
    drain_jobs(c, 3.0)

    res = pc.get(f"{BASE}/v1/results/{stage_id}").json()
    check("participant results released", res.get("released") is True, str(res)[:200])
    check(
        "score breakdown has components",
        any(k in (res.get("by_component") or {}) for k in ("evidence_report", "code_review", "ctf_bonus")),
        str(res.get("by_component")),
    )
    check(
        "CTF flag verified as bonus",
        float((res.get("by_component") or {}).get("ctf_bonus", 0)) > 0,
        str(res.get("by_component")),
    )
    total = res.get("total", 0)
    check("total score positive", total > 0, str(total))

    lb = pc.get(f"{BASE}/v1/leaderboard/{stage_id}").json()
    check("leaderboard visible after release", lb.get("visible") is True and lb.get("snapshot"), str(lb)[:200])

    # ledger audit chain intact
    av = oc.get(f"{BASE}/v1/admin/audit/verify").json()
    check("audit hash chain intact", av.get("intact") is True, str(av)[:200])

    # exports
    ex = oc.get(f"{BASE}/v1/admin/events/{event_id}/export/results")
    check("results CSV export", ex.status_code == 200 and "team" in ex.text.lower()[:200], ex.text[:120])

    # dispute
    r = pc.post(
        f"{BASE}/v1/submissions/{receipt5['submission_id']}/dispute",
        json={"statement": "This duplicate of the delete-bug report was scored as needs_review; please confirm."},
    )
    check("dispute filed", r.status_code == 201, r.text[:200])
    disputes = oc.get(f"{BASE}/v1/admin/disputes").json()["disputes"]
    check("organizer sees dispute", len(disputes) >= 1)
    d = disputes[0]
    r = oc.post(
        f"{BASE}/v1/admin/disputes/{d['id']}/resolve",
        json={
            "outcome": "rejected",
            "resolution": "Report overlaps the already-awarded unit; original decision stands.",
        },
    )
    check("dispute resolved", r.status_code == 200, r.text[:200])

    print("== journey summary ==")
    failed = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
