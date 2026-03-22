from __future__ import annotations

from services import auth_service


def test_hash_password_round_trip():
    password = "VeryStrongPass123"

    encoded = auth_service.hash_password(password)

    assert encoded.startswith("scrypt$")
    assert auth_service.verify_password(password, encoded) is True
    assert auth_service.verify_password("wrong-password", encoded) is False


def test_validate_password_strength_requires_length_and_character_mix():
    assert auth_service.validate_password_strength("short") == "Password must be at least 12 characters."
    assert auth_service.validate_password_strength("alllowercase123") == "Password must include an uppercase letter."
    assert auth_service.validate_password_strength("ALLUPPERCASE123") == "Password must include a lowercase letter."
    assert auth_service.validate_password_strength("NoDigitsHere!!") == "Password must include a number."
    assert auth_service.validate_password_strength("StrongEnough123") == ""


def test_build_action_link_uses_relative_path_without_base_url():
    link = auth_service.build_action_link(token_name="invite_token", token="abc123", base_url="")

    assert link == "?invite_token=abc123"


def test_user_context_admin_detection_prefers_full_access_flag():
    context = auth_service.UserContext(
        user_id="u1",
        email="admin@example.com",
        first_name="Admin",
        last_name="User",
        display_name="Admin User",
        role="investor",
        portfolio_id="p1",
        portfolio_slug="master-portfolio",
        portfolio_name="Master Portfolio",
        membership_role="viewer",
        share_fraction=0.0,
        can_view_full_portfolio=True,
    )

    assert context.is_admin is True
    assert context.label == "Admin User"
