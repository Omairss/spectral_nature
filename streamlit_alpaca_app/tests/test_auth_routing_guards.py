from __future__ import annotations

from pathlib import Path


APP_SOURCE = Path(__file__).resolve().parents[1] / "app.py"


def test_auth_action_urls_block_cookie_restore_and_public_home_shortcut():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert "_force_logged_out_for_auth_action()" in source
    assert "if _auth_action_query_param_present():\n        return False" in source
    assert "and not _auth_action_query_param_present()\n    and _routing_target == \"Home\"" in source

