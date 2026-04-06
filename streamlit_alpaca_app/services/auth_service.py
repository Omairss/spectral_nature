from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import html
import hmac
import os
from pathlib import Path
import re
import secrets
from typing import Any
from urllib.parse import urlencode

from . import auth_store
from .emailer import EmailDeliveryResult, EmailInlineImage, email_delivery_configured, send_email
from .secrets import resolve_secret_value

APP_ROOT = Path(__file__).resolve().parents[1]
INVITE_EMAIL_LOGO_COLOR_PATH = APP_ROOT / "branding" / "Logo Files" / "png" / "Color logo - no background.png"
INVITE_EMAIL_LOGO_WHITE_PATH = APP_ROOT / "branding" / "Logo Files" / "png" / "White logo - no background.png"
INVITE_EMAIL_GRAPH_LIGHT_PATH = APP_ROOT / "branding" / "email" / "invite-performance-graph.png"
INVITE_EMAIL_GRAPH_DARK_PATH = APP_ROOT / "branding" / "email" / "invite-performance-graph-dark.png"
INVITE_EMAIL_LOGO_CID = "sn_invite_logo"
INVITE_EMAIL_GRAPH_CID = "sn_invite_graph"
INVITE_EMAIL_THEME_LEGACY_SETTING_KEY = "invite_email_theme_v1"
INVITE_EMAIL_TEMPLATE_LIBRARY_SETTING_KEY = "invite_email_template_library_v1"
INVITE_EMAIL_TEMPLATE_DARK_ID = "dark-default"
INVITE_EMAIL_TEMPLATE_WHITE_ID = "white-default"
INVITE_EMAIL_TEMPLATE_NAME_MAX = 80
INVITE_EMAIL_UPLOAD_MAX_BYTES = 4 * 1024 * 1024
INVITE_EMAIL_UPLOAD_ALLOWED_MIME_TYPES = {"image/png", "image/gif"}
INVITE_EMAIL_THEME_WHITE_DEFAULT: dict[str, Any] = {
    "kicker": "Private Client Workspace Access",
    "headline": "Your account invite is ready",
    "intro_text": "Activate your account to review market intelligence, portfolio context, and daily research in a single workspace.",
    "cta_label": "Activate account",
    "graph_caption": "Signal quality typically improves over the first few sessions as market and portfolio context is layered in.",
    "footer_note": "If you did not expect this invitation, you can safely ignore this message.",
    "background_color": "#f3f4f6",
    "card_background_color": "#ffffff",
    "title_color": "#111827",
    "body_color": "#374151",
    "muted_text_color": "#6b7280",
    "button_color": "#111827",
    "button_text_color": "#ffffff",
    "link_color": "#2563eb",
    "border_color": "#e5e7eb",
    "show_graph": True,
}
INVITE_EMAIL_THEME_DARK_DEFAULT: dict[str, Any] = {
    "kicker": "Private Client Workspace Access",
    "headline": "Your account invite is ready",
    "intro_text": "Activate your account to review market intelligence, portfolio context, and daily research in a single workspace.",
    "cta_label": "Activate account",
    "graph_caption": "Signal quality typically improves over the first few sessions as market and portfolio context is layered in.",
    "footer_note": "If you did not expect this invitation, you can safely ignore this message.",
    "background_color": "#0b1220",
    "card_background_color": "#111827",
    "title_color": "#f3f4f6",
    "body_color": "#d1d5db",
    "muted_text_color": "#9ca3af",
    "button_color": "#f3f4f6",
    "button_text_color": "#111827",
    "link_color": "#93c5fd",
    "border_color": "#334155",
    "show_graph": True,
}


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


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_hex_color(value: object, default: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    if len(raw) == 7 and raw.startswith("#"):
        candidate = raw
    elif len(raw) == 6:
        candidate = f"#{raw}"
    else:
        return default
    hex_chars = set("0123456789abcdefABCDEF")
    if all(char in hex_chars for char in candidate[1:]):
        return candidate.lower()
    return default


def _sanitize_theme_text(value: object, default: str, *, max_len: int) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if len(text) > max_len:
        return text[:max_len].strip() or default
    return text


def default_invite_email_theme() -> dict[str, Any]:
    return {key: value for key, value in INVITE_EMAIL_THEME_WHITE_DEFAULT.items()}


def sanitize_invite_email_theme(theme: dict[str, Any] | None) -> dict[str, Any]:
    base = default_invite_email_theme()
    payload = theme if isinstance(theme, dict) else {}
    base["kicker"] = _sanitize_theme_text(payload.get("kicker"), base["kicker"], max_len=120)
    base["headline"] = _sanitize_theme_text(payload.get("headline"), base["headline"], max_len=160)
    base["intro_text"] = _sanitize_theme_text(payload.get("intro_text"), base["intro_text"], max_len=700)
    base["cta_label"] = _sanitize_theme_text(payload.get("cta_label"), base["cta_label"], max_len=60)
    base["graph_caption"] = _sanitize_theme_text(payload.get("graph_caption"), base["graph_caption"], max_len=240)
    base["footer_note"] = _sanitize_theme_text(payload.get("footer_note"), base["footer_note"], max_len=240)
    base["background_color"] = _normalize_hex_color(payload.get("background_color"), base["background_color"])
    base["card_background_color"] = _normalize_hex_color(payload.get("card_background_color"), base["card_background_color"])
    base["title_color"] = _normalize_hex_color(payload.get("title_color"), base["title_color"])
    base["body_color"] = _normalize_hex_color(payload.get("body_color"), base["body_color"])
    base["muted_text_color"] = _normalize_hex_color(payload.get("muted_text_color"), base["muted_text_color"])
    base["button_color"] = _normalize_hex_color(payload.get("button_color"), base["button_color"])
    base["button_text_color"] = _normalize_hex_color(payload.get("button_text_color"), base["button_text_color"])
    base["link_color"] = _normalize_hex_color(payload.get("link_color"), base["link_color"])
    base["border_color"] = _normalize_hex_color(payload.get("border_color"), base["border_color"])
    base["show_graph"] = _coerce_bool(payload.get("show_graph"), default=bool(base["show_graph"]))
    return base


def _sanitize_template_id(value: object) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return ""
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", candidate):
        return candidate
    return ""


def _slugify_template_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        slug = f"template-{secrets.token_hex(4)}"
    return slug[:64]


def _sanitize_template_name(value: object, default: str) -> str:
    return _sanitize_theme_text(value, default, max_len=INVITE_EMAIL_TEMPLATE_NAME_MAX)


def _sanitize_logo_variant(value: object, default: str = "color") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"color", "white"}:
        return normalized
    return default


def _sanitize_chart_asset(asset: dict[str, Any] | None, *, fallback_kind: str = "light") -> dict[str, Any]:
    fallback = {"kind": "builtin", "name": "dark" if fallback_kind == "dark" else "light"}
    if not isinstance(asset, dict):
        return fallback
    kind = str(asset.get("kind") or "").strip().lower()
    if kind == "builtin":
        name = str(asset.get("name") or "").strip().lower()
        if name in {"light", "dark"}:
            return {"kind": "builtin", "name": name}
        return fallback
    if kind == "upload":
        mime_type = str(asset.get("mime_type") or "").strip().lower()
        data_b64 = str(asset.get("data_b64") or "").strip()
        filename = _sanitize_theme_text(asset.get("filename"), "uploaded-chart", max_len=180)
        if mime_type not in INVITE_EMAIL_UPLOAD_ALLOWED_MIME_TYPES:
            return fallback
        if not data_b64:
            return fallback
        try:
            raw = base64.b64decode(data_b64.encode("ascii"), validate=True)
        except Exception:
            return fallback
        if not raw or len(raw) > INVITE_EMAIL_UPLOAD_MAX_BYTES:
            return fallback
        return {
            "kind": "upload",
            "mime_type": mime_type,
            "filename": filename,
            "data_b64": data_b64,
        }
    return fallback


def _default_chart_asset(kind: str) -> dict[str, Any]:
    return {"kind": "builtin", "name": "dark" if str(kind or "").strip().lower() == "dark" else "light"}


def _default_template_payloads() -> dict[str, dict[str, Any]]:
    return {
        INVITE_EMAIL_TEMPLATE_DARK_ID: {
            "template_id": INVITE_EMAIL_TEMPLATE_DARK_ID,
            "name": "Dark (Default)",
            "theme": {key: value for key, value in INVITE_EMAIL_THEME_DARK_DEFAULT.items()},
            "logo_variant": "white",
            "chart_asset": _default_chart_asset("dark"),
            "protected": True,
        },
        INVITE_EMAIL_TEMPLATE_WHITE_ID: {
            "template_id": INVITE_EMAIL_TEMPLATE_WHITE_ID,
            "name": "White (Current)",
            "theme": {key: value for key, value in INVITE_EMAIL_THEME_WHITE_DEFAULT.items()},
            "logo_variant": "color",
            "chart_asset": _default_chart_asset("light"),
            "protected": True,
        },
    }


def _sanitize_template_payload(
    template_id: str,
    payload: dict[str, Any] | None,
    *,
    default_name: str,
    default_theme: dict[str, Any],
    default_logo_variant: str,
    default_chart_kind: str,
    protected: bool,
) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    theme_payload = data.get("theme")
    if isinstance(theme_payload, dict):
        theme = sanitize_invite_email_theme({**default_theme, **theme_payload})
    else:
        theme = sanitize_invite_email_theme(default_theme)
    return {
        "template_id": template_id,
        "name": _sanitize_template_name(data.get("name"), default_name),
        "theme": theme,
        "logo_variant": _sanitize_logo_variant(data.get("logo_variant"), default_logo_variant),
        "chart_asset": _sanitize_chart_asset(data.get("chart_asset"), fallback_kind=default_chart_kind),
        "protected": bool(protected),
    }


def _normalize_template_library(raw: dict[str, Any] | None) -> dict[str, Any]:
    defaults = _default_template_payloads()
    raw_templates = raw.get("templates") if isinstance(raw, dict) else None
    templates_obj = raw_templates if isinstance(raw_templates, dict) else {}
    templates: dict[str, dict[str, Any]] = {}

    for builtin_id, default_payload in defaults.items():
        templates[builtin_id] = _sanitize_template_payload(
            builtin_id,
            templates_obj.get(builtin_id),
            default_name=str(default_payload.get("name") or builtin_id),
            default_theme=dict(default_payload.get("theme") or {}),
            default_logo_variant=str(default_payload.get("logo_variant") or "color"),
            default_chart_kind=str((default_payload.get("chart_asset") or {}).get("name") or "light"),
            protected=True,
        )

    for raw_id, raw_payload in templates_obj.items():
        template_id = _sanitize_template_id(raw_id)
        if not template_id or template_id in templates:
            continue
        templates[template_id] = _sanitize_template_payload(
            template_id,
            raw_payload if isinstance(raw_payload, dict) else None,
            default_name=template_id.replace("-", " ").title(),
            default_theme=INVITE_EMAIL_THEME_WHITE_DEFAULT,
            default_logo_variant="color",
            default_chart_kind="light",
            protected=False,
        )

    active_id = _sanitize_template_id(raw.get("active_template_id") if isinstance(raw, dict) else "")
    if not active_id or active_id not in templates:
        active_id = INVITE_EMAIL_TEMPLATE_DARK_ID

    return {"active_template_id": active_id, "templates": templates}


def _load_legacy_theme_if_any() -> dict[str, Any] | None:
    legacy = auth_store.get_app_setting(INVITE_EMAIL_THEME_LEGACY_SETTING_KEY)
    value = legacy.get("value") if isinstance(legacy, dict) else None
    if not isinstance(value, dict):
        return None
    return sanitize_invite_email_theme(value)


def _load_invite_template_library() -> dict[str, Any]:
    stored = auth_store.get_app_setting(INVITE_EMAIL_TEMPLATE_LIBRARY_SETTING_KEY)
    value = stored.get("value") if isinstance(stored, dict) else None
    library = _normalize_template_library(value if isinstance(value, dict) else None)
    if not isinstance(value, dict):
        legacy_theme = _load_legacy_theme_if_any()
        if isinstance(legacy_theme, dict):
            white_template = dict(library["templates"].get(INVITE_EMAIL_TEMPLATE_WHITE_ID) or {})
            white_template["theme"] = sanitize_invite_email_theme(legacy_theme)
            library["templates"][INVITE_EMAIL_TEMPLATE_WHITE_ID] = white_template
    return library


def _serialize_template_library(library: dict[str, Any]) -> dict[str, Any]:
    templates_out: dict[str, Any] = {}
    templates = library.get("templates")
    if isinstance(templates, dict):
        for template_id, template in templates.items():
            if not isinstance(template, dict):
                continue
            templates_out[template_id] = {
                "name": str(template.get("name") or template_id),
                "theme": sanitize_invite_email_theme(template.get("theme") if isinstance(template.get("theme"), dict) else {}),
                "logo_variant": _sanitize_logo_variant(template.get("logo_variant"), "color"),
                "chart_asset": _sanitize_chart_asset(
                    template.get("chart_asset") if isinstance(template.get("chart_asset"), dict) else None,
                    fallback_kind="dark" if template_id == INVITE_EMAIL_TEMPLATE_DARK_ID else "light",
                ),
            }
    return {
        "active_template_id": _sanitize_template_id(library.get("active_template_id"))
        or INVITE_EMAIL_TEMPLATE_DARK_ID,
        "templates": templates_out,
    }


def _persist_invite_template_library(library: dict[str, Any], *, updated_by: UserContext | None = None) -> dict[str, Any]:
    serialized = _serialize_template_library(library)
    auth_store.set_app_setting(
        INVITE_EMAIL_TEMPLATE_LIBRARY_SETTING_KEY,
        serialized,
        updated_by=(updated_by.user_id if isinstance(updated_by, UserContext) else None),
    )
    return _normalize_template_library(serialized)


def _ordered_template_ids(templates: dict[str, dict[str, Any]]) -> list[str]:
    custom_ids = sorted(
        [template_id for template_id in templates if template_id not in {INVITE_EMAIL_TEMPLATE_DARK_ID, INVITE_EMAIL_TEMPLATE_WHITE_ID}],
        key=lambda item: str((templates.get(item) or {}).get("name") or item).lower(),
    )
    ordered: list[str] = []
    for builtin_id in [INVITE_EMAIL_TEMPLATE_DARK_ID, INVITE_EMAIL_TEMPLATE_WHITE_ID]:
        if builtin_id in templates:
            ordered.append(builtin_id)
    ordered.extend(custom_ids)
    return ordered


def get_invite_email_template_library() -> dict[str, Any]:
    library = _load_invite_template_library()
    templates = library.get("templates") if isinstance(library.get("templates"), dict) else {}
    template_list = [templates[template_id] for template_id in _ordered_template_ids(templates) if template_id in templates]
    return {
        "active_template_id": str(library.get("active_template_id") or INVITE_EMAIL_TEMPLATE_DARK_ID),
        "templates": template_list,
    }


def get_active_invite_email_template() -> dict[str, Any]:
    library = _load_invite_template_library()
    templates = library.get("templates") if isinstance(library.get("templates"), dict) else {}
    active_id = str(library.get("active_template_id") or INVITE_EMAIL_TEMPLATE_DARK_ID)
    active_template = templates.get(active_id)
    if isinstance(active_template, dict):
        return active_template
    return templates.get(INVITE_EMAIL_TEMPLATE_DARK_ID) or _default_template_payloads()[INVITE_EMAIL_TEMPLATE_DARK_ID]


def get_invite_email_theme() -> dict[str, Any]:
    template = get_active_invite_email_template()
    return sanitize_invite_email_theme(template.get("theme") if isinstance(template.get("theme"), dict) else None)


def set_active_invite_email_template(
    template_id: str,
    *,
    updated_by: UserContext | None = None,
) -> dict[str, Any]:
    library = _load_invite_template_library()
    normalized_id = _sanitize_template_id(template_id)
    if not normalized_id or normalized_id not in library["templates"]:
        raise ValueError("Template not found.")
    library["active_template_id"] = normalized_id
    persisted = _persist_invite_template_library(library, updated_by=updated_by)
    return {
        "active_template_id": persisted["active_template_id"],
        "template": persisted["templates"].get(normalized_id),
    }


def save_invite_email_template(
    *,
    template_name: str,
    theme: dict[str, Any],
    logo_variant: str,
    chart_asset: dict[str, Any] | None,
    template_id: str | None = None,
    updated_by: UserContext | None = None,
) -> dict[str, Any]:
    library = _load_invite_template_library()
    templates = library.get("templates") if isinstance(library.get("templates"), dict) else {}

    requested_id = _sanitize_template_id(template_id) if template_id else ""
    if requested_id and requested_id in templates:
        resolved_id = requested_id
    else:
        resolved_id = _slugify_template_name(template_name)
        while resolved_id in templates:
            resolved_id = f"{_slugify_template_name(template_name)[:52]}-{secrets.token_hex(3)}"

    existing = templates.get(resolved_id) if isinstance(templates.get(resolved_id), dict) else None
    protected = bool((existing or {}).get("protected")) or resolved_id in {INVITE_EMAIL_TEMPLATE_DARK_ID, INVITE_EMAIL_TEMPLATE_WHITE_ID}
    default_chart_kind = "dark" if resolved_id == INVITE_EMAIL_TEMPLATE_DARK_ID else "light"
    sanitized_template = _sanitize_template_payload(
        resolved_id,
        {
            "name": template_name,
            "theme": theme,
            "logo_variant": logo_variant,
            "chart_asset": chart_asset,
        },
        default_name=(existing or {}).get("name") or template_name or resolved_id.replace("-", " ").title(),
        default_theme=(existing or {}).get("theme") if isinstance((existing or {}).get("theme"), dict) else INVITE_EMAIL_THEME_WHITE_DEFAULT,
        default_logo_variant=str((existing or {}).get("logo_variant") or "color"),
        default_chart_kind=default_chart_kind,
        protected=protected,
    )
    templates[resolved_id] = sanitized_template
    library["templates"] = templates
    library["active_template_id"] = resolved_id
    persisted = _persist_invite_template_library(library, updated_by=updated_by)
    return {
        "created": existing is None,
        "active_template_id": persisted["active_template_id"],
        "template": persisted["templates"].get(resolved_id),
    }


def delete_invite_email_template(
    template_id: str,
    *,
    updated_by: UserContext | None = None,
) -> dict[str, Any]:
    normalized_id = _sanitize_template_id(template_id)
    if not normalized_id:
        return {"ok": False, "message": "Template id is required."}
    if normalized_id in {INVITE_EMAIL_TEMPLATE_DARK_ID, INVITE_EMAIL_TEMPLATE_WHITE_ID}:
        return {"ok": False, "message": "Built-in templates cannot be deleted."}
    library = _load_invite_template_library()
    templates = library.get("templates") if isinstance(library.get("templates"), dict) else {}
    if normalized_id not in templates:
        return {"ok": False, "message": "Template not found."}
    templates.pop(normalized_id, None)
    if str(library.get("active_template_id") or "") == normalized_id:
        library["active_template_id"] = INVITE_EMAIL_TEMPLATE_DARK_ID
    persisted = _persist_invite_template_library(library, updated_by=updated_by)
    return {"ok": True, "active_template_id": persisted["active_template_id"]}


def save_invite_email_theme(theme: dict[str, Any], *, updated_by: UserContext | None = None) -> dict[str, Any]:
    active_template = get_active_invite_email_template()
    result = save_invite_email_template(
        template_name=str(active_template.get("name") or "Invite Template"),
        theme=sanitize_invite_email_theme(theme),
        logo_variant=str(active_template.get("logo_variant") or "color"),
        chart_asset=active_template.get("chart_asset") if isinstance(active_template.get("chart_asset"), dict) else None,
        template_id=str(active_template.get("template_id") or ""),
        updated_by=updated_by,
    )
    template = result.get("template") if isinstance(result, dict) else None
    return sanitize_invite_email_theme(template.get("theme") if isinstance(template, dict) else None)


def _resolve_invite_template(template_override: dict[str, Any] | None = None) -> dict[str, Any]:
    base = get_active_invite_email_template()
    if not isinstance(template_override, dict):
        return base

    theme_base = base.get("theme") if isinstance(base.get("theme"), dict) else default_invite_email_theme()
    override_theme = template_override.get("theme")
    if isinstance(override_theme, dict):
        theme = sanitize_invite_email_theme({**theme_base, **override_theme})
    else:
        theme = sanitize_invite_email_theme({**theme_base, **template_override})

    logo_variant = _sanitize_logo_variant(template_override.get("logo_variant"), str(base.get("logo_variant") or "color"))
    fallback_kind = "dark" if logo_variant == "white" else "light"
    chart_asset = _sanitize_chart_asset(
        template_override.get("chart_asset") if isinstance(template_override.get("chart_asset"), dict) else base.get("chart_asset"),
        fallback_kind=fallback_kind,
    )
    return {
        "template_id": str(base.get("template_id") or ""),
        "name": _sanitize_template_name(template_override.get("name"), str(base.get("name") or "Invite Template")),
        "theme": theme,
        "logo_variant": logo_variant,
        "chart_asset": chart_asset,
        "protected": bool(base.get("protected")),
    }


def _load_inline_email_image(path: Path, *, content_id: str, mime_type: str = "image/png") -> EmailInlineImage | None:
    try:
        payload = path.read_bytes()
    except Exception:
        return None
    if not payload:
        return None
    return EmailInlineImage(
        content_id=content_id,
        content=payload,
        mime_type=mime_type,
        filename=path.name,
    )


def _load_logo_inline_image_for_variant(logo_variant: str, *, content_id: str) -> EmailInlineImage | None:
    variant = _sanitize_logo_variant(logo_variant, "color")
    if variant == "white":
        return _load_inline_email_image(
            INVITE_EMAIL_LOGO_WHITE_PATH,
            content_id=content_id,
            mime_type="image/png",
        )
    return _load_inline_email_image(
        INVITE_EMAIL_LOGO_COLOR_PATH,
        content_id=content_id,
        mime_type="image/png",
    )


def _load_chart_inline_image_from_asset(
    chart_asset: dict[str, Any] | None,
    *,
    content_id: str,
    fallback_kind: str,
) -> EmailInlineImage | None:
    sanitized_asset = _sanitize_chart_asset(chart_asset if isinstance(chart_asset, dict) else None, fallback_kind=fallback_kind)
    kind = str(sanitized_asset.get("kind") or "").strip().lower()
    if kind == "builtin":
        name = str(sanitized_asset.get("name") or "").strip().lower()
        if name == "dark":
            return _load_inline_email_image(
                INVITE_EMAIL_GRAPH_DARK_PATH,
                content_id=content_id,
                mime_type="image/png",
            )
        return _load_inline_email_image(
            INVITE_EMAIL_GRAPH_LIGHT_PATH,
            content_id=content_id,
            mime_type="image/png",
        )
    if kind != "upload":
        return None

    mime_type = str(sanitized_asset.get("mime_type") or "").strip().lower()
    filename = _sanitize_theme_text(sanitized_asset.get("filename"), "uploaded-chart", max_len=180)
    data_b64 = str(sanitized_asset.get("data_b64") or "").strip()
    if mime_type not in INVITE_EMAIL_UPLOAD_ALLOWED_MIME_TYPES or not data_b64:
        return None
    try:
        payload = base64.b64decode(data_b64.encode("ascii"), validate=True)
    except Exception:
        return None
    if not payload or len(payload) > INVITE_EMAIL_UPLOAD_MAX_BYTES:
        return None
    return EmailInlineImage(
        content_id=content_id,
        content=payload,
        mime_type=mime_type,
        filename=filename,
    )


def _invite_email_text(*, invite_url: str, expires_at: datetime, theme: dict[str, Any] | None = None) -> str:
    resolved_theme = sanitize_invite_email_theme(theme)
    headline = str(resolved_theme.get("headline") or "").strip()
    intro_text = str(resolved_theme.get("intro_text") or "").strip()
    footer_note = str(resolved_theme.get("footer_note") or "").strip()
    cta_label = str(resolved_theme.get("cta_label") or "").strip()
    return (
        "You have been invited to access Spectral Nature.\n\n"
        f"{headline}\n\n"
        f"{intro_text}\n\n"
        f"{cta_label}:\n{invite_url}\n\n"
        "This secure link grants access to your client workspace and is single-use.\n"
        f"Link expiration: {expires_at.strftime('%Y-%m-%d %H:%M UTC')}.\n\n"
        f"{footer_note}"
    )


def _invite_email_html(
    *,
    invite_url: str,
    expires_at: datetime,
    recipient_email: str,
    role: str,
    logo_src: str | None,
    graph_src: str | None,
    theme: dict[str, Any] | None = None,
) -> str:
    resolved_theme = sanitize_invite_email_theme(theme)
    kicker = html.escape(str(resolved_theme.get("kicker") or ""))
    headline = html.escape(str(resolved_theme.get("headline") or ""))
    intro_text = html.escape(str(resolved_theme.get("intro_text") or ""))
    cta_label = html.escape(str(resolved_theme.get("cta_label") or ""))
    graph_caption = html.escape(str(resolved_theme.get("graph_caption") or ""))
    footer_note = html.escape(str(resolved_theme.get("footer_note") or ""))
    background_color = html.escape(str(resolved_theme.get("background_color") or "#f3f4f6"))
    card_background_color = html.escape(str(resolved_theme.get("card_background_color") or "#ffffff"))
    title_color = html.escape(str(resolved_theme.get("title_color") or "#111827"))
    body_color = html.escape(str(resolved_theme.get("body_color") or "#374151"))
    muted_text_color = html.escape(str(resolved_theme.get("muted_text_color") or "#6b7280"))
    button_color = html.escape(str(resolved_theme.get("button_color") or "#111827"))
    button_text_color = html.escape(str(resolved_theme.get("button_text_color") or "#ffffff"))
    link_color = html.escape(str(resolved_theme.get("link_color") or "#2563eb"))
    border_color = html.escape(str(resolved_theme.get("border_color") or "#e5e7eb"))
    show_graph = bool(resolved_theme.get("show_graph"))

    recipient_label = html.escape(recipient_email)
    role_label = html.escape(str(role or "investor").strip().title() or "Investor")
    expiry_label = html.escape(expires_at.strftime("%A, %B %d, %Y at %H:%M UTC"))
    invite_href = html.escape(invite_url, quote=True)
    invite_text = html.escape(invite_url)

    logo_block = (
        f'<img src="{html.escape(str(logo_src or ""), quote=True)}" alt="Spectral Nature" width="220" style="display:block; border:0; outline:none; text-decoration:none;">'
        if logo_src
        else f'<div style="font-size:26px; line-height:1.2; color:{title_color}; font-weight:700;">Spectral Nature</div>'
    )
    graph_block = (
        "<tr>"
        '<td style="padding:0 40px 28px 40px;">'
        '<img src="{src}" alt="Portfolio intelligence trend chart" width="520" '
        'style="display:block; width:100%; max-width:520px; height:auto; border-radius:14px; border:1px solid {border_color};">'
        '<div style="padding-top:10px; font-size:12px; line-height:18px; color:{muted_text_color};">'
        "{graph_caption}"
        "</div>"
        "</td>"
        "</tr>"
    ).format(
        src=html.escape(str(graph_src or ""), quote=True),
        border_color=border_color,
        muted_text_color=muted_text_color,
        graph_caption=graph_caption,
    ) if show_graph and graph_src else ""

    return (
        "<!doctype html>"
        "<html lang='en'>"
        "<head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'></head>"
        f"<body style='margin:0; padding:0; background-color:{background_color}; font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,Helvetica,Arial,sans-serif;'>"
        f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='background:{background_color}; padding:24px 12px;'>"
        "<tr><td align='center'>"
        f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='max-width:620px; background:{card_background_color}; border-radius:18px; overflow:hidden; border:1px solid {border_color};'>"
        f"<tr><td style='padding:26px 40px 18px 40px; background:{card_background_color};'>"
        f"{logo_block}"
        f"<div style='margin-top:8px; font-size:12px; color:{muted_text_color}; letter-spacing:0.2px;'>by Torres Capital</div>"
        "</td></tr>"
        "<tr><td style='padding:8px 40px 0 40px;'>"
        f"<div style='font-size:13px; color:{muted_text_color}; margin-bottom:8px;'>{kicker}</div>"
        f"<div style='font-size:28px; line-height:34px; color:{title_color}; font-weight:700;'>{headline}</div>"
        "</td></tr>"
        f"<tr><td style='padding:16px 40px 0 40px; font-size:16px; line-height:24px; color:{title_color};'>"
        f"This invitation was issued for <strong>{recipient_label}</strong> with role <strong>{role_label}</strong>."
        "</td></tr>"
        f"<tr><td style='padding:10px 40px 24px 40px; font-size:15px; line-height:24px; color:{body_color};'>"
        f"{intro_text}"
        "</td></tr>"
        f"{graph_block}"
        "<tr><td align='center' style='padding:0 40px 20px 40px;'>"
        f"<a href='{invite_href}' style='display:inline-block; background:{button_color}; color:{button_text_color}; text-decoration:none; font-weight:600; font-size:15px; line-height:20px; padding:14px 24px; border-radius:10px;'>{cta_label}</a>"
        "</td></tr>"
        f"<tr><td style='padding:0 40px 8px 40px; font-size:13px; line-height:20px; color:{muted_text_color};'>"
        "If the button does not open, copy and paste this link into your browser:"
        "</td></tr>"
        "<tr><td style='padding:0 40px 18px 40px;'>"
        f"<a href='{invite_href}' style='font-size:13px; color:{link_color}; word-break:break-all;'>{invite_text}</a>"
        "</td></tr>"
        "<tr><td style='padding:0 40px 28px 40px;'>"
        f"<div style='font-size:13px; color:{muted_text_color};'>This secure link expires on <strong style='color:{title_color};'>{expiry_label}</strong>.</div>"
        "</td></tr>"
        f"<tr><td style='padding:18px 40px; border-top:1px solid {border_color}; font-size:12px; line-height:18px; color:#9ca3af;'>"
        "Spectral Nature by Torres Capital<br>"
        f"{footer_note}"
        "</td></tr>"
        "</table>"
        "</td></tr>"
        "</table>"
        "</body>"
        "</html>"
    )


def _image_data_uri(image: EmailInlineImage | None) -> str | None:
    if image is None or not image.content:
        return None
    mime_type = str(image.mime_type or "image/png").strip() or "image/png"
    encoded = base64.b64encode(image.content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_invite_email_preview(
    *,
    invite_url: str,
    recipient_email: str,
    role: str,
    expires_at: datetime | None = None,
    template_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_expires = expires_at or (_now_utc() + timedelta(hours=_invite_ttl_hours()))
    template = _resolve_invite_template(template_override)
    theme = template.get("theme") if isinstance(template.get("theme"), dict) else default_invite_email_theme()
    logo_variant = str(template.get("logo_variant") or "color")
    chart_asset = template.get("chart_asset") if isinstance(template.get("chart_asset"), dict) else None
    fallback_kind = "dark" if logo_variant == "white" else "light"

    logo_image = _load_logo_inline_image_for_variant(
        logo_variant,
        content_id=INVITE_EMAIL_LOGO_CID,
    )
    graph_image = _load_chart_inline_image_from_asset(
        chart_asset,
        content_id=INVITE_EMAIL_GRAPH_CID,
        fallback_kind=fallback_kind,
    )
    html_body = _invite_email_html(
        invite_url=invite_url,
        expires_at=resolved_expires,
        recipient_email=recipient_email,
        role=role,
        logo_src=_image_data_uri(logo_image),
        graph_src=_image_data_uri(graph_image),
        theme=theme,
    )
    text_body = _invite_email_text(
        invite_url=invite_url,
        expires_at=resolved_expires,
        theme=theme,
    )
    return {
        "html_body": html_body,
        "text_body": text_body,
        "theme": theme,
        "template": template,
    }


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
    email_theme_override: dict[str, Any] | None = None,
    email_template_override: dict[str, Any] | None = None,
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
        template_override = email_template_override if isinstance(email_template_override, dict) else None
        if isinstance(email_theme_override, dict):
            template_override = dict(template_override or {})
            template_override["theme"] = dict(email_theme_override)
        template = _resolve_invite_template(template_override)
        theme = template.get("theme") if isinstance(template.get("theme"), dict) else default_invite_email_theme()
        logo_variant = str(template.get("logo_variant") or "color")
        chart_asset = template.get("chart_asset") if isinstance(template.get("chart_asset"), dict) else None
        fallback_kind = "dark" if logo_variant == "white" else "light"
        inline_images: list[EmailInlineImage] = []
        logo_image = _load_logo_inline_image_for_variant(
            logo_variant,
            content_id=INVITE_EMAIL_LOGO_CID,
        )
        graph_image = _load_chart_inline_image_from_asset(
            chart_asset,
            content_id=INVITE_EMAIL_GRAPH_CID,
            fallback_kind=fallback_kind,
        )
        if logo_image:
            inline_images.append(logo_image)
        if graph_image and bool(theme.get("show_graph")):
            inline_images.append(graph_image)

        email_result = send_email(
            to_address=str(email or "").strip(),
            subject="Your Spectral Nature account invite",
            text_body=_invite_email_text(
                invite_url=invite_url,
                expires_at=expires_at,
                theme=theme,
            ),
            html_body=_invite_email_html(
                invite_url=invite_url,
                expires_at=expires_at,
                recipient_email=str(email or "").strip(),
                role=role,
                logo_src=f"cid:{logo_image.content_id}" if logo_image else None,
                graph_src=f"cid:{graph_image.content_id}" if graph_image and bool(theme.get("show_graph")) else None,
                theme=theme,
            ),
            inline_images=inline_images,
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


def delete_pending_invite(*, invite_id: str, requested_by: UserContext) -> dict[str, Any]:
    if not isinstance(requested_by, UserContext) or not requested_by.is_admin:
        return {"ok": False, "message": "Only admins can delete pending invites."}
    try:
        deleted = auth_store.delete_pending_invite(
            invite_id=invite_id,
            deleted_by=requested_by.user_id,
        )
    except Exception as exc:
        return {"ok": False, "message": str(exc)}
    payload = deleted if isinstance(deleted, dict) else {}
    return {
        "ok": True,
        "message": "Pending invite deleted.",
        "invite": payload,
    }


__all__ = [
    "UserContext",
    "accept_invite",
    "admin_issue_password_reset",
    "authenticate_user",
    "build_invite_email_preview",
    "build_action_link",
    "complete_password_reset",
    "database_auth_bootstrap_configured",
    "database_auth_enabled",
    "delete_pending_invite",
    "default_invite_email_theme",
    "delete_invite_email_template",
    "generate_token",
    "get_active_invite_email_template",
    "get_invite_email_template_library",
    "get_invite_email_theme",
    "get_invite_preview",
    "hash_password",
    "initialize_auth_system",
    "issue_invite",
    "list_pending_invites",
    "list_users",
    "logout_session",
    "request_password_reset",
    "restore_user_from_session",
    "sanitize_invite_email_theme",
    "save_invite_email_template",
    "save_invite_email_theme",
    "set_active_invite_email_template",
    "token_digest",
    "validate_password_strength",
    "verify_password",
]
