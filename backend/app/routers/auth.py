"""Authentication endpoints (spec AUTH-1..05).

- POST /auth/login: rate limited, generic error (no account enumeration).
- POST /auth/refresh: cookie + CSRF headers; rotates the refresh token family;
  reuse outside the grace window revokes the family (AUTH-4).
- POST /auth/activate: single-use invite token -> set password + accept rules.
- POST /auth/password/reset-request + /reset.
- GET /me, /me/sessions, POST /me/acceptances, POST /auth/step-up.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text

from .. import security
from ..audit import audit_write
from ..config import settings
from ..db import app_session, uuid7
from ..deps import Principal, check_csrf, get_principal, rate_limit, require_admin
from ..errors import ApiError

router = APIRouter(tags=["auth"])

REFRESH_COOKIE = "bhctf_refresh"


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=raw_token,
        max_age=settings.refresh_token_ttl_s,
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        path="/v1/auth/refresh",  # scoped to the refresh route only (spec 6.1)
        domain=settings.cookie_domain or None,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE, path="/v1/auth/refresh", domain=settings.cookie_domain or None)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else ""
    rate_limit(f"login:{ip}", limit=20, window_s=60)
    rate_limit(f"login-u:{body.email.lower()}", limit=10, window_s=60)
    with app_session() as s:
        user = (
            s.execute(
                text(
                    "SELECT id, email, name, role, status, password_hash, token_version "
                    "FROM app.users WHERE lower(email) = :e FOR UPDATE"
                ),
                {"e": body.email.lower()},
            )
            .mappings()
            .first()
        )
        # First sign-in flow: no activation invite needed. Signing in with your
        # email id activates the account. Only registered accounts can sign in;
        # unregistered emails are rejected with a generic error (no enumeration).
        # If you are registered but have no password yet, the password you type
        # becomes your password. If you have a password, it is checked.
        # NOTE: this is a risk we are willing to take for this event.
        if user is None:
            # Only admin-registered accounts can sign in. Keep the rejection generic.
            raise ApiError("AUTH_INVALID_CREDENTIALS")
        if user["status"] == "disqualified":
            raise ApiError("AUTH_FORBIDDEN", message="This account was disqualified. Contact the organizers.")
        if user["status"] not in ("active", "pending"):
            raise ApiError("AUTH_NOT_ACTIVATED")
        if not user["password_hash"]:
            # Event-specific policy: the first successful email sign-in sets the password.
            # This deliberately does not verify mailbox ownership. Admin registration is
            # the trust boundary, and the event accepts the risk of first-claim by email.
            pw_hash = security.hash_password(body.password)
            s.execute(
                text("UPDATE app.users SET password_hash = :pw, status = 'active' WHERE id = CAST(:uid AS uuid)"),
                {"pw": pw_hash, "uid": str(user["id"])},
            )
            user = {**user, "password_hash": pw_hash, "status": "active"}
        elif not security.verify_password(body.password, user["password_hash"]):
            raise ApiError("AUTH_INVALID_CREDENTIALS")
        elif user["status"] == "pending":
            # A pre-existing passworded pending account becomes active only after
            # the supplied password has been verified successfully.
            s.execute(
                text("UPDATE app.users SET status = 'active' WHERE id = CAST(:uid AS uuid)"),
                {"uid": str(user["id"])},
            )
            user = {**user, "status": "active"}

        # Concurrent session limit (AUTH-10), hardened with reclaim + eviction:
        # 1) Reclaim zombie sessions: a session whose refresh tokens are all
        #    expired/revoked can never refresh again, so it must not hold a
        #    slot forever (this was a permanent lockout path).
        # 2) If live sessions are still at the cap, evict the oldest live
        #    session (revoked, token family revoked, audited) so a new sign-in
        #    always succeeds. The cap bounds live sessions; it must never lock
        #    a legitimate user out of their own account.
        # The user-row lock serializes concurrent logins for one user so two
        # racing sign-ins cannot exceed the cap.
        s.execute(text("SELECT id FROM app.users WHERE id = CAST(:u AS uuid) FOR UPDATE"), {"u": str(user["id"])})
        s.execute(
            text(
                "UPDATE app.sessions SET revoked_at = now() "
                "WHERE user_id = CAST(:u AS uuid) AND revoked_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM app.refresh_tokens rt "
                "  WHERE rt.session_id = app.sessions.id AND rt.status = 'active' AND rt.expires_at > now())"
            ),
            {"u": str(user["id"])},
        )
        live = (
            s.execute(
                text(
                    "SELECT id FROM app.sessions WHERE user_id = CAST(:u AS uuid) AND revoked_at IS NULL ORDER BY created_at ASC"
                ),
                {"u": str(user["id"])},
            )
            .scalars()
            .all()
        )
        while len(live) >= settings.max_sessions_per_user:
            victim = str(live.pop(0))
            s.execute(text("UPDATE app.sessions SET revoked_at = now() WHERE id = CAST(:sid AS uuid)"), {"sid": victim})
            s.execute(
                text("UPDATE app.refresh_tokens SET status='revoked' WHERE session_id = CAST(:sid AS uuid)"),
                {"sid": victim},
            )
            audit_write(
                s,
                actor_id=str(user["id"]),
                actor_role=user["role"],
                action="auth.session_evicted",
                entity_type="session",
                entity_id=victim,
                request_id=request.state.request_id,
            )

        session_id = str(uuid7())
        s.execute(
            text(
                "INSERT INTO app.sessions (id, user_id, ip_hash, user_agent) VALUES "
                "(CAST(:id AS uuid), CAST(:u AS uuid), :ip, :ua)"
            ),
            {
                "id": session_id,
                "u": str(user["id"]),
                "ip": security.ip_hash(ip),
                "ua": request.headers.get("user-agent", "")[:300],
            },
        )
        token, exp = security.create_access_token(
            user_id=str(user["id"]), session_id=session_id, role=user["role"], token_version=int(user["token_version"])
        )
        # initial refresh token in the family
        raw_refresh = security.new_refresh_token()
        s.execute(
            text(
                "INSERT INTO app.refresh_tokens (id, session_id, user_id, token_hash, status, expires_at) "
                "VALUES (CAST(:id AS uuid), CAST(:sid AS uuid), CAST(:u AS uuid), :th, 'active', :exp)"
            ),
            {
                "id": str(uuid7()),
                "sid": session_id,
                "u": str(user["id"]),
                "th": security.token_hash(raw_refresh),
                "exp": datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_s),
            },
        )
        audit_write(
            s,
            actor_id=str(user["id"]),
            actor_role=user["role"],
            action="auth.login",
            entity_type="user",
            entity_id=str(user["id"]),
            request_id=request.state.request_id,
        )
    _set_refresh_cookie(response, raw_refresh)
    return {
        "access_token": token,
        "expires_at": exp.isoformat(),
        "server_time": datetime.now(UTC).isoformat(),
        "user": {
            "id": str(user["id"]),
            "email": user["email"],
            "name": user["name"],
            "role": user["role"],
            "status": user["status"],
        },
    }


class RegisterIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2, max_length=160)
    role: str = Field(default="reviewer", pattern="^(reviewer|organizer|admin)$")


@router.post("/auth/register", status_code=201)
def register(body: RegisterIn, request: Request, p: Principal = Depends(require_admin)):
    """Admin-only account registration. The user chooses the initial password at first sign-in."""
    if body.role not in ("reviewer", "organizer", "admin"):
        raise ApiError("VALIDATION_ERROR", details={"role": "participant|reviewer|organizer|admin"})
    # Only an admin may create admin/organizer accounts.
    if body.role in ("admin", "organizer") and p.role != "admin":
        raise ApiError("AUTH_FORBIDDEN", message="Only an admin can create organizer or admin accounts.")
    rate_limit(f"register:{request.client.host if request.client else ''}", limit=10, window_s=60)
    with app_session() as s:
        tenant = s.execute(text("SELECT id FROM app.tenants ORDER BY created_at LIMIT 1")).first()
        if tenant is None:
            raise ApiError("VALIDATION_ERROR", details={"tenant": "No tenant exists. Run the seed script."})
        try:
            s.execute(
                text(
                    "INSERT INTO app.users (id, tenant_id, email, name, role, password_hash, status, registration_id) "
                    "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :email, :name, :role, NULL, 'active', :reg)"
                ),
                {
                    "id": str(uuid7()),
                    "t": str(tenant[0]),
                    "email": body.email.lower(),
                    "name": body.name,
                    "role": body.role,
                    "reg": str(uuid7())[:8],
                },
            )
        except Exception:  # noqa: BLE001 - intentionally broad: never reveal whether email exists (AUTH-1)
            # Generic outcome: never reveal whether the email already exists (AUTH-1).
            raise ApiError("VALIDATION_ERROR", details={"email": "Registration failed. Try again."}) from None
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="auth.registered",
            entity_type="user",
            entity_id=str(body.email.lower()),
            request_id=request.state.request_id,
            details={"email": body.email.lower(), "role": body.role},
        )
    return {
        "ok": True,
        "email": body.email.lower(),
        "message": "Account registered. The user sets their password on first sign-in.",
        "password_setup_required": True,
    }


@router.post("/auth/refresh")
def refresh(request: Request, response: Response):
    check_csrf(request)
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise ApiError("AUTH_SESSION_REVOKED")
    th = security.token_hash(raw)
    with app_session() as s:
        row = (
            s.execute(
                text(
                    "SELECT rt.id, rt.session_id, rt.user_id, rt.status AS rt_status, rt.expires_at, rt.successor_encrypted, "
                    "rt.rotated_at, u.role, u.status AS user_status, u.token_version "
                    "FROM app.refresh_tokens rt JOIN app.users u ON u.id = rt.user_id WHERE rt.token_hash = :th"
                ),
                {"th": th},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError("AUTH_SESSION_REVOKED")
        now = datetime.now(UTC)
        if row["rt_status"] == "revoked" or row["expires_at"] < now:
            raise ApiError("AUTH_SESSION_REVOKED")
        if row["rt_status"] == "rotated":
            # AUTH-4 reuse handling: within grace return the same successor; outside revoke family
            rotated_at = row["rotated_at"]
            in_grace = rotated_at is not None and (now - rotated_at).total_seconds() <= settings.refresh_reuse_grace_s
            if in_grace and row["successor_encrypted"]:
                successor = security.decrypt_successor(row["successor_encrypted"])
                if successor:
                    with app_session() as s2:
                        urow = (
                            s2.execute(
                                text("SELECT id, role, token_version FROM app.users WHERE id = CAST(:u AS uuid)"),
                                {"u": str(row["user_id"])},
                            )
                            .mappings()
                            .first()
                        )
                    token, exp = security.create_access_token(
                        user_id=str(row["user_id"]),
                        session_id=str(row["session_id"]),
                        role=urow["role"],
                        token_version=int(urow["token_version"]),
                    )
                    _set_refresh_cookie(response, successor)
                    return {"access_token": token, "expires_at": exp.isoformat(), "server_time": now.isoformat()}
            # reuse outside the grace window: revoke the family
            s.execute(
                text("UPDATE app.refresh_tokens SET status='revoked' WHERE session_id = CAST(:sid AS uuid)"),
                {"sid": str(row["session_id"])},
            )
            s.execute(
                text("UPDATE app.sessions SET revoked_at = :now WHERE id = CAST(:sid AS uuid)"),
                {"now": now, "sid": str(row["session_id"])},
            )
            # app_session() rolls back when an exception escapes its context.
            # Commit the security response before raising so replay revocation persists.
            s.commit()
            raise ApiError("AUTH_SESSION_REVOKED")

        # normal rotation
        urow = (
            s.execute(
                text("SELECT id, role, status, token_version FROM app.users WHERE id = CAST(:u AS uuid)"),
                {"u": str(row["user_id"])},
            )
            .mappings()
            .first()
        )
        if urow is None or urow["status"] != "active":
            raise ApiError("AUTH_SESSION_REVOKED")
        new_raw = security.new_refresh_token()
        new_id = str(uuid7())
        s.execute(
            text(
                "INSERT INTO app.refresh_tokens (id, session_id, user_id, token_hash, status, replaces_id, expires_at) "
                "VALUES (CAST(:id AS uuid), CAST(:sid AS uuid), CAST(:u AS uuid), :th, 'active', CAST(:repl AS uuid), :exp)"
            ),
            {
                "id": new_id,
                "sid": str(row["session_id"]),
                "u": str(row["user_id"]),
                "th": security.token_hash(new_raw),
                "repl": str(row["id"]),
                "exp": now + timedelta(seconds=settings.refresh_token_ttl_s),
            },
        )
        s.execute(
            text(
                "UPDATE app.refresh_tokens SET status='rotated', rotated_at=:now, successor_encrypted=:enc WHERE id = CAST(:id AS uuid)"
            ),
            {"now": now, "enc": security.encrypt_successor(new_raw), "id": str(row["id"])},
        )
        s.execute(
            text("UPDATE app.sessions SET last_seen_at = :now WHERE id = CAST(:sid AS uuid)"),
            {"now": now, "sid": str(row["session_id"])},
        )
        token, exp = security.create_access_token(
            user_id=str(row["user_id"]),
            session_id=str(row["session_id"]),
            role=urow["role"],
            token_version=int(urow["token_version"]),
        )
    _set_refresh_cookie(response, new_raw)
    return {"access_token": token, "expires_at": exp.isoformat(), "server_time": now.isoformat()}


@router.post("/auth/logout")
def logout(request: Request, p: Principal = Depends(get_principal), response: Response = None):
    with app_session() as s:
        s.execute(
            text("UPDATE app.refresh_tokens SET status='revoked' WHERE session_id = CAST(:sid AS uuid)"),
            {"sid": p.session_id},
        )
        s.execute(
            text("UPDATE app.sessions SET revoked_at = now() WHERE id = CAST(:sid AS uuid)"), {"sid": p.session_id}
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="auth.logout",
            entity_type="user",
            entity_id=p.id,
            request_id=request.state.request_id,
        )
    resp = JSONResponse({"ok": True})
    _clear_refresh_cookie(resp)
    return resp


# Account activation is intentionally handled by first sign-in for this event.


class StepUpIn(BaseModel):
    password: str


@router.post("/auth/step-up")
def step_up(body: StepUpIn, request: Request, p: Principal = Depends(get_principal)):
    with app_session() as s:
        urow = (
            s.execute(text("SELECT password_hash FROM app.users WHERE id = CAST(:u AS uuid)"), {"u": p.id})
            .mappings()
            .first()
        )
        if not urow or not security.verify_password(body.password, urow["password_hash"]):
            rate_limit(f"stepup:{p.id}", limit=5, window_s=300)
            raise ApiError("AUTH_INVALID_CREDENTIALS")
        s.execute(
            text("UPDATE app.sessions SET step_up_at = now() WHERE id = CAST(:sid AS uuid)"), {"sid": p.session_id}
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="auth.step_up",
            entity_type="user",
            entity_id=p.id,
            request_id=request.state.request_id,
        )
    return {"ok": True, "valid_until": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()}


# Self-service password reset is intentionally disabled; admins use the admin reset endpoint.


@router.get("/me")
def me(p: Principal = Depends(get_principal)):
    with app_session() as s:
        team = (
            s.execute(
                text(
                    "SELECT t.id, t.name, t.join_code, e.id AS event_id, e.slug, e.status AS event_status "
                    "FROM app.team_members tm JOIN app.teams t ON t.id = tm.team_id "
                    "JOIN app.events e ON e.id = t.event_id WHERE tm.user_id = CAST(:u AS uuid) LIMIT 1"
                ),
                {"u": p.id},
            )
            .mappings()
            .first()
        )
        current_rules = s.execute(
            text(
                "SELECT rules_version FROM app.events WHERE status IN ('ready','live','closed','finalized') ORDER BY created_at DESC LIMIT 1"
            )
        ).first()
        accepted = None
        if current_rules is not None:
            accepted = s.execute(
                text("SELECT 1 FROM app.acceptances WHERE user_id = CAST(:u AS uuid) AND rules_version = :v"),
                {"u": p.id, "v": int(current_rules[0])},
            ).first()
    out = {
        "id": p.id,
        "email": p.email,
        "name": p.name,
        "role": p.role,
        "status": p.status,
        "team": dict(team) if team else None,
        "rules_accepted": bool(accepted),
        "rules_version": int(current_rules[0]) if current_rules else None,
    }
    return out


@router.get("/me/sessions")
def my_sessions(p: Principal = Depends(get_principal)):
    with app_session() as s:
        rows = (
            s.execute(
                text(
                    "SELECT id, created_at, last_seen_at, user_agent, revoked_at FROM app.sessions "
                    "WHERE user_id = CAST(:u AS uuid) AND revoked_at IS NULL ORDER BY last_seen_at DESC"
                ),
                {"u": p.id},
            )
            .mappings()
            .all()
        )
    return [
        {
            "id": str(r["id"]),
            "created_at": r["created_at"].isoformat(),
            "last_seen_at": r["last_seen_at"].isoformat(),
            "device": (r["user_agent"] or "")[:80],
        }
        for r in rows
    ]


@router.delete("/me/sessions/{session_id}")
def revoke_session(session_id: str, p: Principal = Depends(get_principal)):
    with app_session() as s:
        s.execute(
            text(
                "UPDATE app.sessions SET revoked_at = now() WHERE id = CAST(:sid AS uuid) AND user_id = CAST(:u AS uuid)"
            ),
            {"sid": session_id, "u": p.id},
        )
        s.execute(
            text("UPDATE app.refresh_tokens SET status='revoked' WHERE session_id = CAST(:sid AS uuid)"),
            {"sid": session_id},
        )
    return {"ok": True}


class AcceptIn(BaseModel):
    version: int | None = None


@router.post("/me/acceptances")
def accept_rules(body: AcceptIn, request: Request, p: Principal = Depends(get_principal)):
    with app_session() as s:
        ev = s.execute(
            text(
                "SELECT id, rules_version FROM app.events WHERE status IN ('ready','live','closed','finalized') ORDER BY created_at DESC LIMIT 1"
            )
        ).first()
        if ev is None:
            raise ApiError("NOT_FOUND", message="No published event found.")
        version = body.version or int(ev[1])
        s.execute(
            text(
                "INSERT INTO app.acceptances (id, user_id, rules_version, ip_hash) VALUES "
                "(CAST(:id AS uuid), CAST(:u AS uuid), :v, :ip) ON CONFLICT (user_id, rules_version) DO NOTHING"
            ),
            {
                "id": str(uuid7()),
                "u": p.id,
                "v": version,
                "ip": security.ip_hash(request.client.host if request.client else ""),
            },
        )
        audit_write(
            s,
            actor_id=p.id,
            actor_role=p.role,
            action="rules.accepted",
            entity_type="event",
            entity_id=str(ev[0]),
            request_id=request.state.request_id,
            details={"version": version},
        )
    return {"ok": True, "rules_version": version}
