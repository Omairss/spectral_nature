from __future__ import annotations

from pathlib import Path


APP_SOURCE = Path(__file__).resolve().parents[1] / "app.py"
DEPLOY_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy_ui_azure.sh"


def test_missing_layout_query_clears_stale_session_override():
    source = APP_SOURCE.read_text(encoding="utf-8")

    assert 'st.session_state["_ui_layout_mode_override"] = query_mode\n        selected_mode = query_mode' in source
    assert 'st.session_state.pop("_ui_layout_mode_override", None)' in source


def test_dev_ui_deploy_defaults_to_auto_layout_unless_explicitly_overridden():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'if [[ "$TARGET" == "dev" ]]; then' in source
    assert 'if ! requested_env_override_present "STREAMLIT_MOBILE_UI_ENABLED"; then' in source
    assert 'STREAMLIT_MOBILE_UI_ENABLED="true"' in source
    assert 'if ! requested_env_override_present "STREAMLIT_LAYOUT_MODE_DEFAULT"; then' in source
    assert 'STREAMLIT_LAYOUT_MODE_DEFAULT="auto"' in source
