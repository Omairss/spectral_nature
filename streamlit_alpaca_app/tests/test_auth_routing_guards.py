from __future__ import annotations

from pathlib import Path


APP_SOURCE = Path(__file__).resolve().parents[1] / "app.py"


def test_auth_action_urls_block_cookie_restore_and_public_home_shortcut():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "_force_logged_out_for_auth_action()" in source
    assert "if _auth_action_query_param_present():\n        return False" in source
    assert "and not _auth_action_query_param_present()\n    and _routing_target == \"Home\"" in source


def test_successful_login_consumes_auth_action_query_params():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "_clear_auth_query_params()\n            st.session_state[\"_ui_authenticated\"] = True" in source
    assert "_clear_auth_query_params()\n                    st.session_state[\"_ui_authenticated\"] = True" in source


def test_database_login_does_not_rerun_before_cookie_script_can_mount():
    source = APP_SOURCE.read_text(encoding="utf-8")

    login_cookie_sync = (
        '_render_auth_cookie_sync("set", session_token, persistent=remember_me)\n'
        "                    _apply_post_login_destination()\n"
        "                    return"
    )
    invite_cookie_sync = (
        '_render_auth_cookie_sync("set", session_token, persistent=True)\n'
        "                        _apply_post_login_destination()\n"
        '                        st.success("Account created. Loading your workspace...")\n'
        "                        return"
    )
    assert login_cookie_sync in source
    assert invite_cookie_sync in source
