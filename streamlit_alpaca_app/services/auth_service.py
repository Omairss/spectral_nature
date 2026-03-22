from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import os
import secrets
from typing import Any
from urllib.parse import urlencode

from . import auth_store
from .emailer import EmailDeliveryResult, email_delivery_configured, send_email
from .secrets import resolve_secret_value


@dataclass(frozen=True)
class UserContext:
    user_id: str
    email: str
    first_name: str
    last_name: str
    display_name: str
    role: str
    portfolio_id: str
    portfolio_slug: str
    portfolio_name: str
    membership_role: str
    share_fraction: float
    can_view_full_portfolio: bool = False

    @property
    def label(self) -> str:
        return self.display_name or " ".join(part for part in [self.first_name, self.last_name] if part).strip() or self.email

    @property
    def is_admin(self) -> bool:
        role = str(self.role or "").strip().lower()
        membership_role = str(self.membership_role or "").strip().lower()
        return self.can_view_full_portfolio or role == "admin" or membership_role == "admin"

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "display_name": self.display_name,
            "role": self.role,
            "portfolio_id": self.portfolio_id,
            "portfolio_slug": self.portfolio_slug,
            "portfolio_name": self.portfolio_name,
            "membership_role": self.membership_role,
            "share_fraction": self.share_fraction,
            "can_view_full_portfolio": self.can_view_full_portfolio,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "UserContext | None":
        if not isinstance(payload, dict):
            return None
        return cls(
            user_id=str(payload.get("user_id") or ""),
            email=str(payload.get("email") or "").strip(),
            first_name=str(payload.get("first_name") or "").strip(),
            last_name=str(payload.get("last_name") or "").strip(),
            display_name=str(payload.get("display_name") or "").strip(),
            role=str(payload.get("role") or "").strip(),
            portfolio_id=str(payload.get("portfolio_id") or "").strip(),
            portfolio_slug=str(payload.get("portfolio_slug") or "").strip(),
            portfolio_name=str(payload.get("portfolio_name") or "").strip(),
            membership_role=str(payload.get("membership_role") or "").strip(),
            share_fraction=_coerce_share_fraction(payload.get("share_fraction")),
            can_view_full_portfolio=bool(payload.get("can_view_full_portfolio")),
        )


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_share_fraction(value: object) -> float:
    try:
        return max(0.0, min(float(value or 0.0), 1.0))
    except Exception:
        return 0.0


def auth_mode() -> str:
    raw = (os.getenv("DASHBOARD_AUTH_MODE") or "auto").strip().lower()
    if raw in {"database", "legacy", "auto"}:
        return raw
    return "auto"


def database_auth_bootstrap_configured() -> bool:
    return bool(_bootstrap_admin_email() and _bootstrap_admin_password())


def _bootstrap_admin_email() -> str:
    return resolve_secret_value(
        ["DASHBOARD_AUTH_BOOTSTRAP_ADMIN_EMAIL"],
        secret_name_env="DASHBOARD_AUTH_BOOTSTRAP_ADMIN_EMAIL_SECRET",
        default_secret_name="dashboard-bootstrap-admin-email",
    )


def _bootstrap_admin_password() -> str:
    return resolve_secret_value(
        ["DASHBOARD_AUTH_BOOTSTRAP_ADMIN_PASSWORD"],
        secret_name_env="DASHBOARD_AUTH_BOOTSTRAP_ADMIN_PASSWORD_SECRET",
        default_secret_name="dashboard-bootstrap-admin-password",
    )


def _bootstrap_admin_first_name() -> str:
    return (os.getenv("DASHBOARD_AUTH_BOOTSTRAP_ADMIN_FIRST_NAME") or "Admin").strip() or "Admin"


def _bootstrap_admin_last_name() -> str:
    return (os.getenv("DASHBOARD_AUTH_BOOTSTRAP_ADMIN_LAST_NAME") or "User").strip() or "User"


def _session_ttl_seconds() -> int:
    raw = (os.getenv("AUTH_SESSION_TTL_SECONDS") or str(7 * 24 * 60 * 60)).strip()
    try:
        return max(int(raw), 300)
    except Exception:
        return 7 * 24 * 60 * 60


def _invite_ttl_hours() -> int:
    raw = (os.getenv("AUTH_INVITE_TTL_HOURS") or "72").strip()
    try:
        return max(int(raw), 1)
    except Exception:
        return 72


def _reset_ttl_minutes() -> int:
    raw = (os.getenv("AUTH_PASSWORD_RESET_TTL_MINUTES") or "30").strip()
    try:
        return max(int(raw), 5)
    except Exception:
        return 30


def _max_failed_attempts() -> int:
    raw = (os.getenv("AUTH_MAX_FAILED_ATTEMPTS") or "5").strip()
    try:
        return max(int(raw), 1)
    except Exception:
        return 5


def _lockout_minutes() -> int:
    raw = (os.getenv("AUTH_LOCKOUT_MINUTES") or "15").strip()
    try:
        return max(int(raw), 1)
    except Exception:
        return 15


def _public_base_url() -> str:
    return (os.getenv("APP_PUBLIC_BASE_URL") or "").strip().rstrip("/")


def build_action_link(*, token_name: str, token: str, base_url: str = "") -> str:
    root = (base_url or _public_base_url()).strip().rstrip("/")
    query = urlencode({token_name: token})
    if root:
        return f"{root}/?{query}"
    return f"?{query}"


def token_digest(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def validate_password_strength(password: str) -> str:
    value = str(password or "")
    if len(value) < 12:
        return "Password must be at least 12 characters."
    if not any(char.islower() for char in value):
        return "Password must include a lowercase letter."
    if not any(char.isupper() for char in value):
        return "Password must include an uppercase letter."
    if not any(char.isdigit() for char in value):
        return "Password must include a number."
    return ""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n_value = 2 ** 14
    r_value = 8
    p_value = 1
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n_value, r=r_value, p=p_value, dklen=64)
    salt_blob = base64.urlsafe_b64encode(salt).decode("ascii")
    key_blob = base64.urlsafe_b64encode(derived).decode("ascii")
    return f"scrypt${n_value}${r_value}${p_value}${salt_blob}${key_blob}"


def verify_password(password: str, encoded_hash: str) -> bool:
    parts = str(encoded_hash or "").split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n_value = int(parts[1])
        r_value = int(parts[2])
        p_value = int(parts[3])
        salt = base64.urlsafe_b64decode(parts[4].encode("ascii"))
        expected = base64.urlsafe_b64decode(parts[5].encode("ascii"))
    except Exception:
        return False
    try:
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n_value, r=r_value, p=p_value, dklen=len(expected))
    except Exception:
        return False
    return hmac.compare_digest(derived, expected)


def database_auth_enabled() -> bool:
    if not auth_store.auth_store_configured():
        return False
    mode = auth_mode()
    if mode == "database":
        return True
    if mode == "legacy":
        return False
    if auth_store.has_users() or database_auth_bootstrap_configured():
        return True
    legacy_username = (os.getenv("DASHBOARD_AUTH_USERNAME") or "").strip()
    legacy_password = (os.getenv("DASHBOARD_AUTH_PASSWORD") or "").strip()
    return not (legacy_username and legacy_password)


def initialize_auth_system() -> dict[str, Any]:
    available = auth_store.auth_store_configured()
    if not available:
        return {
            "available": False,
            "ready": False,
            "has_users": False,
            "email_delivery": email_delivery_configured(),
            "message": "Authentication database is unavailable.",
        }

    ready = auth_store.ensure_auth_schema()
    has_users = auth_store.has_users() if ready else False
    bootstrapped = False
    if ready and (not has_users) and database_auth_bootstrap_configured():
        password_error = validate_password_strength(_bootstrap_admin_password())
        if not password_error:
            auth_store.bootstrap_admin(
                email=_bootstrap_admin_email(),
                password_hash=hash_password(_bootstrap_admin_password()),
                first_name=_bootstrap_admin_first_name(),
                last_name=_bootstrap_admin_last_name(),
                display_name="",
            )
            bootstrapped = True
            has_users = auth_store.has_users()
    return {
        "available": available,
        "ready": ready and has_users,
        "has_users": has_users,
        "bootstrapped": bootstrapped,
        "email_delivery": email_delivery_configured(),
        "message": "",
    }


def _context_from_row(row: dict[str, Any] | None) -> UserContext | None:
    if not isinstance(row, dict):
        return None
    return UserContext.from_dict(row)


def authenticate_user(
    *,
    email: str,
    password: str,
    user_agent: str = "",
    ip_address: str = "",
) -> dict[str, Any]:
    record = auth_store.get_user_for_login(email)
    generic_error = "Invalid email or password."
    if record is None:
        return {"ok": False, "message": generic_error}

    if str(record.get("status") or "").strip().lower() != "active":
        return {"ok": False, "message": generic_error}

    locked_until = record.get("locked_until")
    if isinstance(locked_until, datetime) and locked_until > _now_utc():
        return {"ok": False, "message": f"Account is temporarily locked until {locked_until.strftime('%Y-%m-%d %H:%M UTC')}."}

    if not verify_password(password, str(record.get("password_hash") or "")):
        auth_store.record_failed_login(
            email,
            max_attempts=_max_failed_attempts(),
            lockout_until=_now_utc() + timedelta(minutes=_lockout_minutes()),
        )
        return {"ok": False, "message": generic_error}

    context = _context_from_row(record)
    if context is None:
        return {"ok": False, "message": "Account is missing an active portfolio membership."}

    auth_store.clear_failed_login(context.user_id)
    session_token = generate_token()
    auth_store.create_session(
        user_id=context.user_id,
        session_token_hash=token_digest(session_token),
        expires_at=_now_utc() + timedelta(seconds=_session_ttl_seconds()),
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return {
        "ok": True,
        "message": "",
        "context": context,
        "session_token": session_token,
    }


def restore_user_from_session(session_token: str) -> UserContext | None:
    if not session_token:
        return None
    row = auth_store.get_user_context_for_session(token_digest(session_token))
    return _context_from_row(row)


def logout_session(session_token: str) -> None:
    if not session_token:
        return
    auth_store.revoke_session(token_digest(session_token))


def issue_invite(
    *,
    email: str,
    role: str,
    share_fraction: float | None,
    created_by: UserContext,
    base_url: str = "",
) -> dict[str, Any]:
    if not created_by.is_admin:
        return {"ok": False, "message": "Only admins can create invites."}

    token = generate_token()
    expires_at = _now_utc() + timedelta(hours=_invite_ttl_hours())
    try:
        invite = auth_store.insert_invite(
            email=email,
            role=role,
            proposed_share_fraction=share_fraction,
            invite_token_hash=token_digest(token),
            expires_at=expires_at,
            created_by=created_by.user_id,
        )
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

    invite_url = build_action_link(token_name="invite_token", token=token, base_url=base_url)
    email_result = EmailDeliveryResult(False, "Email delivery is not configured.")
    if email_delivery_configured() and (base_url or _public_base_url()):
        email_result = send_email(
            to_address=str(email or "").strip(),
            subject="Your Spectral Nature account invite",
            text_body=(
                "You have been invited to access Spectral Nature.\n\n"
                f"Create your account using this link:\n{invite_url}\n\n"
                f"This link expires on {expires_at.strftime('%Y-%m-%d %H:%M UTC')}."
            ),
        )
    return {
        "ok": True,
        "message": "Invite created.",
        "invite": invite,
        "invite_url": invite_url,
        "email_sent": email_result.sent,
        "email_message": email_result.message,
    }


def accept_invite(
    *,
    invite_token: str,
    first_name: str,
    last_name: str,
    display_name: str,
    password: str,
    user_agent: str = "",
    ip_address: str = "",
) -> dict[str, Any]:
    password_error = validate_password_strength(password)
    if password_error:
        return {"ok": False, "message": password_error}

    try:
        row = auth_store.accept_invite(
            invite_token_hash=token_digest(invite_token),
            first_name=str(first_name or "").strip(),
            last_name=str(last_name or "").strip(),
            display_name=str(display_name or "").strip(),
            password_hash=hash_password(password),
        )
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

    context = _context_from_row(row)
    if context is None:
        return {"ok": False, "message": "Account activation succeeded, but login context could not be created."}

    session_token = generate_token()
    auth_store.create_session(
        user_id=context.user_id,
        session_token_hash=token_digest(session_token),
        expires_at=_now_utc() + timedelta(seconds=_session_ttl_seconds()),
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return {
        "ok": True,
        "message": "Account created successfully.",
        "context": context,
        "session_token": session_token,
    }


def request_password_reset(
    *,
    email: str,
    requested_ip: str = "",
    base_url: str = "",
) -> dict[str, Any]:
    generic_message = "If an account exists for that email, reset instructions have been sent."
    user_row = auth_store.get_active_user_by_email(email)
    if user_row is None:
        return {
            "ok": True,
            "message": generic_message,
            "email_sent": False,
            "email_message": "No matching active account.",
        }

    token = generate_token()
    expires_at = _now_utc() + timedelta(minutes=_reset_ttl_minutes())
    auth_store.issue_password_reset(
        user_id=str(user_row.get("user_id") or ""),
        reset_token_hash=token_digest(token),
        expires_at=expires_at,
        requested_ip=requested_ip,
    )

    reset_url = build_action_link(token_name="reset_token", token=token, base_url=base_url)
    email_result = EmailDeliveryResult(False, "Email delivery is not configured.")
    if email_delivery_configured() and (base_url or _public_base_url()):
        email_result = send_email(
            to_address=str(user_row.get("email") or "").strip(),
            subject="Reset your Spectral Nature password",
            text_body=(
                "A password reset was requested for your Spectral Nature account.\n\n"
                f"Reset your password using this link:\n{reset_url}\n\n"
                f"This link expires on {expires_at.strftime('%Y-%m-%d %H:%M UTC')}."
            ),
        )

    return {
        "ok": True,
        "message": generic_message,
        "email_sent": email_result.sent,
        "email_message": email_result.message,
        "reset_url": reset_url,
    }


def admin_issue_password_reset(
    *,
    email: str,
    requested_by: UserContext,
    base_url: str = "",
) -> dict[str, Any]:
    if not requested_by.is_admin:
        return {"ok": False, "message": "Only admins can issue password reset links."}

    user_row = auth_store.get_active_user_by_email(email)
    if user_row is None:
        return {"ok": False, "message": "No active account found for that email."}

    token = generate_token()
    expires_at = _now_utc() + timedelta(minutes=_reset_ttl_minutes())
    auth_store.issue_password_reset(
        user_id=str(user_row.get("user_id") or ""),
        reset_token_hash=token_digest(token),
        expires_at=expires_at,
        requested_ip="admin",
    )
    reset_url = build_action_link(token_name="reset_token", token=token, base_url=base_url)
    email_result = EmailDeliveryResult(False, "Email delivery is not configured.")
    if email_delivery_configured() and (base_url or _public_base_url()):
        email_result = send_email(
            to_address=str(user_row.get("email") or "").strip(),
            subject="Your Spectral Nature password reset link",
            text_body=(
                "An administrator issued a password reset for your Spectral Nature account.\n\n"
                f"Use this link to set a new password:\n{reset_url}\n\n"
                f"This link expires on {expires_at.strftime('%Y-%m-%d %H:%M UTC')}."
            ),
        )
    return {
        "ok": True,
        "message": "Password reset issued.",
        "reset_url": reset_url,
        "email_sent": email_result.sent,
        "email_message": email_result.message,
    }


def complete_password_reset(*, reset_token: str, new_password: str) -> dict[str, Any]:
    password_error = validate_password_strength(new_password)
    if password_error:
        return {"ok": False, "message": password_error}

    try:
        row = auth_store.reset_password(
            reset_token_hash=token_digest(reset_token),
            password_hash=hash_password(new_password),
        )
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

    context = _context_from_row(row)
    return {
        "ok": True,
        "message": "Password reset complete. Please log in with your new password.",
        "context": context,
    }


def get_invite_preview(invite_token: str) -> dict[str, Any] | None:
    if not invite_token:
        return None
    return auth_store.get_pending_invite_by_token_hash(token_digest(invite_token))


def list_users() -> list[dict[str, Any]]:
    return auth_store.list_users()


def list_pending_invites() -> list[dict[str, Any]]:
    return auth_store.list_pending_invites()


__all__ = [
    "UserContext",
    "accept_invite",
    "admin_issue_password_reset",
    "authenticate_user",
    "build_action_link",
    "complete_password_reset",
    "database_auth_bootstrap_configured",
    "database_auth_enabled",
    "generate_token",
    "get_invite_preview",
    "hash_password",
    "initialize_auth_system",
    "issue_invite",
    "list_pending_invites",
    "list_users",
    "logout_session",
    "request_password_reset",
    "restore_user_from_session",
    "token_digest",
    "validate_password_strength",
    "verify_password",
]
