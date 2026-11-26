"""Development seed for the Bug Hunt CTF platform (DEV/TEST PROFILES ONLY).

Creates deterministic demo data that exercises every role and state:
- tenant, users (admin, organizer, 2 reviewers, 6 participants in 2 teams)
- the "Fall Back" event with the official proposal preset (schedule, scoring
  weights, CTF policy) and readiness checklist
- one stage with frozen rule set, challenge "papyrus" ingested from
  ../challenge-app (source files only; the organizer answer key file is
  EXCLUDED from participant-visible storage)
- ground truth (10 bugs, 2 flags) seeded from the manifest below with
  pattern-anchored locations (DAT-06)
- rules document, announcements, invites (activation tokens printed once)

Never run against a live deployment: known credentials are dev-only.
Usage: python seed.py [--reset]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
CHALLENGE_DIR = BACKEND.parent / "challenge-app"
sys.path.insert(0, str(BACKEND))

from app import security  # noqa: E402
from app.audit import audit_write  # noqa: E402
from app.db import app_session, gt_session, uuid7  # noqa: E402
from sqlalchemy import text  # noqa: E402

# Files served to participants (answer key deliberately excluded).
PARTICIPANT_FILES = [
    "package.json",
    "server.js",
    "db.js",
    "routes/auth.js",
    "routes/notes.js",
    "routes/comments.js",
    "public/index.html",
    "public/app.js",
    "public/styles.css",
    "public/admin.html",
    "README.md",
]

EVENT_CONFIG = {
    "features": {"runner_enabled": False, "ai_assessment_enabled": False, "telemetry_enabled": False},
    "scoring": {
        "weights": {"functional_test": 0.5, "code_review": 0.3, "evidence_report": 0.2},
        "ctf_bonus_cap_points": 100,
        "default_review_quality": 0.8,
    },
    "schedule": [
        {"phase": "Registration and Technical Setup", "duration_minutes": 15},
        {"phase": "Event Briefing and Instructions", "duration_minutes": 10},
        {"phase": "Part 1: Setup and Bug Reconnaissance", "duration_minutes": 30},
        {"phase": "Part 2: Debugging and Root-Cause Analysis", "duration_minutes": 35},
        {"phase": "Part 3: Full-Stack Repair and CTF Side Quests", "duration_minutes": 60},
        {"phase": "Part 4: Testing, Verification and Demonstration", "duration_minutes": 25},
        {"phase": "Final Evaluation and Results", "duration_minutes": 5},
    ],
    "public_summary": {
        "mode": "offline",
        "team_size": "4-5 participants",
        "duration_minutes": 180,
        "main_score": "restoring application functionality",
        "ctf": "optional bonus, capped",
    },
    "capacity_note": "Provisional dev/test caps: 50 participants / 12 teams. Final caps must be load-tested (ADR-011).",
}

STAGE_CONFIG = {
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

# (code, file, line pattern, title, severity, category, points, root_cause_group, explanation, aliases, locations+)
GT_BUGS = [
    (
        "BUG-01",
        "db.js",
        r"WHERE n\.title LIKE \? OR n\.body LIKE \?",
        "Search never matches notes",
        "medium",
        "input_validation",
        25,
        "search-binding",
        "The prepared statement binds the raw term so LIKE compares the exact full value; wildcards are never added.",
        [("like", 1.0), ("wildcard", 0.8), ("search", 0.6)],
        [("routes/notes.js", r"res\.json\(\{ notes: db\.q\.searchNotes\.all\(term, term\)", False)],
    ),
    (
        "BUG-02",
        "routes/notes.js",
        r"res\.status\(201\)\.json\(\{ id: info\.changes",
        "Save response reports the wrong note id",
        "medium",
        "logic",
        25,
        "create-response-id",
        "The response returns info.changes (a rowcount) instead of info.lastInsertRowid, so the client banner links to the wrong note.",
        [("changes", 1.0), ("lastinsertrowid", 0.8), ("insert", 0.5), ("rowid", 0.5)],
        [("db.js", r"createNote: db\.prepare", False)],
    ),
    (
        "BUG-03",
        "public/app.js",
        r"list\.innerHTML = comments\.comments",
        "Comments execute injected markup (controlled XSS)",
        "critical",
        "injection",
        50,
        "comment-render",
        "Comment bodies are interpolated into innerHTML without escaping, so markup posted as a comment executes in every reader's browser.",
        [("innerhtml", 1.0), ("xss", 1.0), ("escape", 0.6), ("onerror", 0.6)],
        [("routes/comments.js", r'router\.post\("/:noteId"', False)],
    ),
    (
        "BUG-04",
        "routes/notes.js",
        r"String\(new Date\(\)\.getUTCSeconds\(\)\)",
        "Last edited shows meaningless digits",
        "low",
        "logic",
        15,
        "updated-at-units",
        "updated_at stores the seconds component of the current time instead of an ISO timestamp.",
        [("getutcseconds", 1.0), ("iso", 0.6), ("updated", 0.5), ("timestamp", 0.5)],
        [("db.js", r"updateNote: db\.prepare", False)],
    ),
    (
        "BUG-05",
        "routes/notes.js",
        r"db\.q\.deleteNote\.run\(note\.author_id\)",
        "Deleting a note removes a different note than requested",
        "high",
        "data_handling",
        40,
        "delete-target",
        "The route passes note.author_id where the query expects the note id, so the request silently deletes the note whose id equals the caller's user id.",
        [("author", 1.0), ("delete", 0.8), ("wrong note", 0.8), ("id mismatch", 0.5)],
        [("db.js", r"deleteNote: db\.prepare", False)],
    ),
    (
        "BUG-06",
        "db.js",
        r"ORDER BY c\.created_at DESC LIMIT 100",
        "Comments render in reverse order",
        "low",
        "logic",
        15,
        "comment-order",
        "The comment query orders created_at DESC while the product expects chronological ascending order.",
        [("desc", 1.0), ("asc", 0.8), ("order", 0.6), ("chronological", 0.6)],
        [("routes/comments.js", r'router\.get\("/:noteId"', False)],
    ),
    (
        "BUG-07",
        "routes/auth.js",
        r'router\.post\("/register"',
        "Registration crashes with a raw 500 on duplicates",
        "medium",
        "input_validation",
        25,
        "register-validation",
        "No validation runs before the INSERT; the UNIQUE constraint error escapes as an unhandled exception.",
        [("unique", 1.0), ("constraint", 0.8), ("validation", 0.8), ("500", 0.4)],
        [("db.js", r"createUser: db\.prepare", False)],
    ),
    (
        "BUG-08",
        "server.js",
        r"const SESSION_TTL_MS = CONFIG_TTL_DAYS \* 60 \* 60 \* 24;",
        "Sessions expire after ~10 minutes instead of 7 days",
        "high",
        "configuration",
        40,
        "cookie-ttl-units",
        "CONFIG_TTL_DAYS (days) is multiplied as if it were milliseconds, collapsing the intended 7-day TTL to ~10 minutes.",
        [("maxage", 1.0), ("ttl", 0.8), ("cookie", 0.8), ("units", 0.6)],
        [("routes/auth.js", r"Max-Age=\$\{Math\.floor\(ttlMs\(\) / 1000\)\}", False)],
    ),
    (
        "BUG-09",
        "db.js",
        r"\(SELECT count\(\*\) FROM notes\) AS notes_total",
        "Profile notes total counts everyone's notes",
        "medium",
        "logic",
        25,
        "author-stats",
        "The stats subquery counts every row of notes with no author filter.",
        [("count", 1.0), ("stats", 0.8), ("profile", 0.6), ("author", 0.6)],
        [("routes/auth.js", r'router\.get\("/me"', False)],
    ),
    (
        "BUG-10",
        "public/app.js",
        r'document\.querySelector\("\.search-go"\)',
        "Search button does nothing (Enter works)",
        "low",
        "integration_api_contract",
        15,
        "search-handler",
        "The click handler queries the .search-go class selector but the button only has the search-go id, so the listener never attaches.",
        [("queryselector", 1.0), ("class", 0.6), ("id", 0.6), ("handler", 0.5)],
        [("public/index.html", r'id="search-go"', False)],
    ),
]

GT_FLAGS = [
    (
        "FLAG-1",
        "FLAG{client_gates_1e_0nly_9f2a}",
        30,
        99,
        "Admin notice returned by the unauthenticated admin overview API (broken access control).",
    ),
    (
        "FLAG-2",
        "FLAG{base64_1s_n0t_encrypt10n_44e2}",
        30,
        99,
        "Base64-encoded string in the onboarding comment of public/app.js.",
    ),
]

RULES_MD = """# Fall Back - Competition Rules (v1, draft for organizer review)

## 1. Participation
- Teams of 4-5 participants, registered by the Software Development Club, VIT Bhopal University.
- Accounts are personal; sharing an account between people is prohibited.
- The event is offline, at the venue, on 6 December 2026, for 180 minutes.

## 2. Main task
- Every team receives the same preconfigured full-stack application with
  intentionally planted defects. Inspect it, reproduce failures, fix them, and
  submit structured bug reports through the platform before the deadline.
- Reports need a target file, a line range (max 25 lines), a category, a title,
  and an explanation. Evidence is required for borderline matches.

## 3. Scoring (provisional, frozen before the event)
- Main score: functional tests 50%, code review 30%, evidence/report quality 20%.
- Verified CTF flags award bonus points, capped at 100 points total per team.
- Claimed severity is informational; points come from the ground truth.
- Definitively invalid reports may carry a small capped penalty, applied only
  at finalization. Plausible but unmatched reports go to human review.
- Tie-breakers: total score, then fewer invalid reports, then earlier last
  accepted finding.

## 4. Conduct and scope
- Attack ONLY the provided challenge application on your own machine.
- Attacking the platform, the venue network, other teams, or any external
  system is prohibited and grounds for disqualification.
- Do not share findings, code, or flags between teams. Screenshots of the
  challenge and external AI tools are prohibited for the main task.
- Feedback on reports is hidden until results are released; probing the
  evaluation with template reports is a violation.

## 5. Appeals
- After results are released, teams may dispute any result through the
  platform within the announced window. Resolutions are final.

## 6. Platform security safe harbor
- Found a vulnerability in the platform itself? Report it privately to the
  organizers immediately. Good-faith reports are welcomed and will not be
  penalized.

Version 1 - draft prepared for organizer/legal review before publication.
"""


def find_line(path: Path, pattern: str) -> int | None:
    rx = re.compile(pattern)
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if rx.search(line):
            return i
    return None


def anchor_hash(path: Path, lineno: int) -> str:
    lines = path.read_text().splitlines()
    window = "\n".join(lines[max(0, lineno - 2) : lineno + 1])
    return hashlib.sha256(window.encode()).hexdigest()


def main(reset: bool) -> None:
    if reset:
        with app_session() as s:
            for stmt in (
                "TRUNCATE app.score_ledger_entries RESTART IDENTITY CASCADE",
                "DELETE FROM app.award_claims",
                "DELETE FROM app.reviews",
                "DELETE FROM app.review_items",
                "DELETE FROM app.disputes",
                "DELETE FROM app.submissions",
                "DELETE FROM app.drafts",
                "DELETE FROM app.jobs",
                "DELETE FROM app.outbox_events",
                "DELETE FROM app.leaderboard_snapshots",
                "DELETE FROM app.assignments",
                "DELETE FROM app.challenge_file_lines",
                "DELETE FROM app.challenge_files",
                "DELETE FROM app.stage_transitions",
                "DELETE FROM app.time_adjustments",
                "DELETE FROM app.stages",
                "DELETE FROM app.team_members",
                "DELETE FROM app.teams",
                "DELETE FROM app.acceptances",
                "DELETE FROM app.refresh_tokens",
                "DELETE FROM app.sessions",
                "DELETE FROM app.announcements",
                "DELETE FROM app.exports",
                "DELETE FROM app.event_config_revisions",
                "DELETE FROM app.users",
                "DELETE FROM app.readiness_items",
                "DELETE FROM app.rules_documents",
                "DELETE FROM app.challenge_versions",
                "DELETE FROM app.events",
                "DELETE FROM app.tenants",
            ):
                s.execute(text(stmt))
        with gt_session() as g:
            for stmt in (
                "DELETE FROM gt.gt_aliases",
                "DELETE FROM gt.gt_locations",
                "DELETE FROM gt.gt_test_cases",
                "DELETE FROM gt.gt_bugs",
                "DELETE FROM gt.gt_flags",
                "DELETE FROM gt.gt_revisions",
            ):
                g.execute(text(stmt))
        print("existing data cleared")

    now = datetime.now(UTC)
    with app_session() as s:
        tenant_id = str(uuid7())
        s.execute(
            text(
                "INSERT INTO app.tenants (id, slug, name) VALUES (CAST(:i AS uuid), 'sdc-vitb', 'Software Development Club, VIT Bhopal University')"
            ),
            {"i": tenant_id},
        )

        users = {}
        for email, name, role in [
            ("admin@example.com", "Platform Admin", "admin"),
            ("organizer@example.com", "Kalpesh Parashar", "organizer"),
            ("organizer2@example.com", "Aashish Prasad", "organizer"),
            ("reviewer1@example.com", "Reviewer One", "reviewer"),
            ("reviewer2@example.com", "Reviewer Two", "reviewer"),
        ]:
            uid = str(uuid7())
            users[email] = uid
            s.execute(
                text(
                    "INSERT INTO app.users (id, tenant_id, email, name, role, password_hash, status) VALUES (CAST(:i AS uuid), CAST(:t AS uuid), :e, :n, :r, :p, 'active')"
                ),
                {
                    "i": uid,
                    "t": tenant_id,
                    "e": email,
                    "n": name,
                    "r": role,
                    "p": security.hash_password("Dev-only!Passw0rd!"),
                },
            )

        event_id = str(uuid7())
        s.execute(
            text(
                "INSERT INTO app.events (id, tenant_id, slug, name, status, timezone, duration_minutes, "
                "max_participants, max_teams, rules_version, config) VALUES "
                "(CAST(:i AS uuid), CAST(:t AS uuid), 'fall-back-2026', 'Fall Back - Full-Stack Software Debugging Challenge', "
                "'configured', 'Asia/Kolkata', 180, 50, 10, 1, CAST(:c AS jsonb))"
            ),
            {"i": event_id, "t": tenant_id, "c": json.dumps(EVENT_CONFIG)},
        )
        s.execute(
            text(
                "INSERT INTO app.rules_documents (id, event_id, version, body_md, content_hash, frozen_at) "
                "VALUES (CAST(:i AS uuid), CAST(:e AS uuid), 1, :b, :h, now())"
            ),
            {"i": str(uuid7()), "e": event_id, "b": RULES_MD, "h": hashlib.sha256(RULES_MD.encode()).hexdigest()},
        )
        for key, label, owner in [
            ("load_test_50", "Load test at the approved cap passes thresholds", "infrastructure lead"),
            ("gt_isolation", "app_api cannot read gt (tested with the real role)", "application lead"),
            ("backup_restore", "Backup/restore rehearsal completed and measured", "infrastructure lead"),
            ("pen_test", "Security review completed; high/critical closed", "application lead"),
            ("challenge_audit", "Two independent reviewers certified the challenge (unplanned defects)", "review lead"),
            ("dry_run", "Full dry run with real users on the venue network", "event lead"),
            ("rules_published", "Rules, privacy notice and consent flows published", "organizer"),
            ("runbooks", "Event-day runbook rehearsed; roles assigned", "event lead"),
        ]:
            s.execute(
                text(
                    "INSERT INTO app.readiness_items (id, event_id, key, label, owner) VALUES (CAST(:i AS uuid), CAST(:e AS uuid), :k, :l, :o)"
                ),
                {"i": str(uuid7()), "e": event_id, "k": key, "l": label, "o": owner},
            )

        stage_id = str(uuid7())
        rsh = (
            "sha256:"
            + hashlib.sha256(json.dumps(STAGE_CONFIG, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        )
        s.execute(
            text(
                "INSERT INTO app.stages (id, event_id, name, kind, ordinal, status, config, rule_set_hash) "
                "VALUES (CAST(:i AS uuid), CAST(:e AS uuid), 'Fall Back Main Challenge', 'main', 1, 'draft', CAST(:c AS jsonb), :rsh)"
            ),
            {"i": stage_id, "e": event_id, "c": json.dumps(STAGE_CONFIG), "rsh": rsh},
        )

        # --- challenge version from challenge-app/ --------------------------------
        challenge_id = str(uuid7())
        instructions = Path(CHALLENGE_DIR / "README.md").read_text()
        s.execute(
            text(
                "INSERT INTO app.challenge_versions (id, event_id, slug, title, version, instructions_md) "
                "VALUES (CAST(:i AS uuid), CAST(:e AS uuid), 'papyrus', 'Papyrus - Campus Notes (challenge edition)', 1, :inst)"
            ),
            {"i": challenge_id, "e": event_id, "inst": instructions},
        )
        line_index = []
        for rel in PARTICIPANT_FILES:
            path = CHALLENGE_DIR / rel
            content = path.read_text()
            sha = hashlib.sha256(content.encode()).hexdigest()
            s.execute(
                text(
                    "INSERT INTO app.challenge_files (id, challenge_version_id, path, lang, content, sha256, size_bytes, line_count) "
                    "VALUES (CAST(:i AS uuid), CAST(:c AS uuid), :p, :lang, :content, :sha, :size, :lines)"
                ),
                {
                    "i": str(uuid7()),
                    "c": challenge_id,
                    "p": rel,
                    "lang": _lang(rel),
                    "content": content,
                    "sha": sha,
                    "size": len(content.encode()),
                    "lines": content.count("\n") + 1,
                },
            )
            for ln, line in enumerate(content.splitlines()[:5000], start=1):
                line_index.append((rel, ln, line[:500]))
        for rel, ln, txt in line_index:
            s.execute(
                text(
                    "INSERT INTO app.challenge_file_lines (id, challenge_version_id, path, line_number, line_text) VALUES (CAST(:i AS uuid), CAST(:c AS uuid), :p, :ln, :lt)"
                ),
                {"i": str(uuid7()), "c": challenge_id, "p": rel, "ln": ln, "lt": txt},
            )
        s.execute(
            text(
                "UPDATE app.challenge_versions SET status='published', content_hash=:h, frozen_at=now(), published_at=now() WHERE id = CAST(:i AS uuid)"
            ),
            {
                "h": "sha256:" + hashlib.sha256(json.dumps(list(PARTICIPANT_FILES)).encode()).hexdigest(),
                "i": challenge_id,
            },
        )
        s.execute(
            text(
                "INSERT INTO app.assignments (id, event_id, stage_id, challenge_version_id, repo_url) VALUES "
                "(CAST(:i AS uuid), CAST(:e AS uuid), CAST(:s AS uuid), CAST(:c AS uuid), :repo)"
            ),
            {"i": str(uuid7()), "e": event_id, "s": stage_id, "c": challenge_id, "repo": None},
        )

        # --- teams, participants, invites ----------------------------------------
        teams = {}
        for team_name, member_emails in [
            (
                "Team Lambda",
                ["p1.team1@example.com", "p2.team1@example.com", "p3.team1@example.com", "p4.team1@example.com"],
            ),
            (
                "Team Vector",
                [
                    "p1.team2@example.com",
                    "p2.team2@example.com",
                    "p3.team2@example.com",
                    "p4.team2@example.com",
                    "p5.team2@example.com",
                ],
            ),
        ]:
            team_id = str(uuid7())
            join_code = secrets.token_hex(4).upper()
            teams[team_name] = team_id
            s.execute(
                text(
                    "INSERT INTO app.teams (id, event_id, name, join_code) VALUES (CAST(:i AS uuid), CAST(:e AS uuid), :n, :jc)"
                ),
                {"i": team_id, "e": event_id, "n": team_name, "jc": join_code},
            )
            for email in member_emails:
                uid = str(uuid7())
                users[email] = uid
                s.execute(
                    text(
                        "INSERT INTO app.users (id, tenant_id, email, name, role, password_hash, status) VALUES (CAST(:i AS uuid), CAST(:t AS uuid), :e, :n, 'participant', :p, 'active')"
                    ),
                    {
                        "i": uid,
                        "t": tenant_id,
                        "e": email,
                        "n": email.split("@")[0].replace(".", " ").title(),
                        "p": security.hash_password("Dev-only!Passw0rd!"),
                    },
                )
                s.execute(
                    text(
                        "INSERT INTO app.team_members (id, team_id, user_id) VALUES (CAST(:i AS uuid), CAST(:t AS uuid), CAST(:u AS uuid))"
                    ),
                    {"i": str(uuid7()), "t": team_id, "u": uid},
                )

        s.execute(
            text(
                "INSERT INTO app.announcements (id, event_id, severity, body, created_by) VALUES (CAST(:i AS uuid), CAST(:e AS uuid), 'info', :b, CAST(:u AS uuid))"
            ),
            {
                "i": str(uuid7()),
                "e": event_id,
                "b": "Welcome to Fall Back! Set up your environment before the briefing ends.",
                "u": users["organizer@example.com"],
            },
        )
        audit_write(
            s,
            actor_id=None,
            actor_role="system",
            action="seed.completed",
            entity_type="event",
            entity_id=event_id,
            request_id="seed",
            details={"users": len(users), "teams": len(teams)},
        )

    with gt_session() as g:
        for (
            code,
            filename,
            pattern,
            title,
            severity,
            category,
            points,
            rcg,
            explanation,
            aliases,
            extra_locs,
        ) in GT_BUGS:
            bug_id = str(uuid7())
            g.execute(
                text(
                    "INSERT INTO gt.gt_bugs (id, challenge_version_id, code, title, severity, category, points, "
                    "root_cause_group, explanation) VALUES (CAST(:i AS uuid), CAST(:c AS uuid), :code, :t, :sev, :cat, :pts, :rcg, :expl)"
                ),
                {
                    "i": bug_id,
                    "c": challenge_id,
                    "code": code,
                    "t": title,
                    "sev": severity,
                    "cat": category,
                    "pts": points,
                    "rcg": rcg,
                    "expl": explanation,
                },
            )
            path = CHALLENGE_DIR / filename
            lineno = find_line(path, pattern)
            if lineno is None:
                print(f"WARNING: anchor pattern not found for {code} in {filename}")
                continue
            g.execute(
                text(
                    "INSERT INTO gt.gt_locations (id, bug_id, file_path, line_start, line_end, is_primary, anchor_hash) VALUES (CAST(:i AS uuid), CAST(:b AS uuid), :fp, :ls, :le, true, :ah)"
                ),
                {
                    "i": str(uuid7()),
                    "b": bug_id,
                    "fp": filename,
                    "ls": max(1, lineno - 1),
                    "le": lineno + 1,
                    "ah": anchor_hash(path, lineno),
                },
            )
            for loc_file, loc_pattern, primary in extra_locs:
                lp = CHALLENGE_DIR / loc_file
                lno = find_line(lp, loc_pattern)
                if lno is None:
                    continue
                g.execute(
                    text(
                        "INSERT INTO gt.gt_locations (id, bug_id, file_path, line_start, line_end, is_primary, anchor_hash) VALUES (CAST(:i AS uuid), CAST(:b AS uuid), :fp, :ls, :le, :prim, :ah)"
                    ),
                    {
                        "i": str(uuid7()),
                        "b": bug_id,
                        "fp": loc_file,
                        "ls": max(1, lno - 1),
                        "le": lno + 1,
                        "prim": primary,
                        "ah": anchor_hash(lp, lno),
                    },
                )
            for kw, w in aliases:
                g.execute(
                    text(
                        "INSERT INTO gt.gt_aliases (id, bug_id, keyword, weight) VALUES (CAST(:i AS uuid), CAST(:b AS uuid), :kw, :w)"
                    ),
                    {"i": str(uuid7()), "b": bug_id, "kw": kw, "w": w},
                )
        for flag_key, plaintext, points, max_awards, note in GT_FLAGS:
            g.execute(
                text(
                    "INSERT INTO gt.gt_flags (id, challenge_version_id, flag_key, code_hash, points, max_awards, position_label) VALUES (CAST(:i AS uuid), CAST(:c AS uuid), :k, :h, :p, :m, :n)"
                ),
                {
                    "i": str(uuid7()),
                    "c": challenge_id,
                    "k": flag_key,
                    "h": security.flag_code_hash(plaintext),
                    "p": points,
                    "m": max_awards,
                    "n": note,
                },
            )
        g.execute(
            text(
                "INSERT INTO gt.gt_revisions (id, challenge_version_id, revision_no, reason, payload) VALUES (CAST(:i AS uuid), CAST(:c AS uuid), 1, 'initial seed', CAST(:p AS jsonb))"
            ),
            {
                "i": str(uuid7()),
                "c": challenge_id,
                "p": json.dumps({"bugs": [b[0] for b in GT_BUGS], "flags": [f[0] for f in GT_FLAGS]}),
            },
        )

    print("seed complete:")
    print(f"  event fall-back-2026, stage {stage_id[:8]}, challenge papyrus ({len(PARTICIPANT_FILES)} files)")
    print(
        f"  ground truth: {len(GT_BUGS)} bugs ({sum(b[6] for b in GT_BUGS)} pts), {len(GT_FLAGS)} flags ({sum(f[2] for f in GT_FLAGS)} pts)"
    )
    print("  staff: admin@ / organizer@ / reviewer1@ / reviewer2@ example.com - password Dev-only!Passw0rd!")
    print("  participants: p1..p5.team1@ / p1..p5.team2@ example.com - password Dev-only!Passw0rd!")

def _lang(rel: str) -> str:
    ext = rel.rsplit(".", 1)[-1]
    return {"js": "javascript", "html": "html", "css": "css", "json": "json", "md": "markdown"}.get(ext, "text")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="clear existing rows first")
    args = parser.parse_args()
    main(args.reset)
