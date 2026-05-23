from __future__ import annotations

from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_feature_pages_do_not_render_job_trigger_buttons():
    source = APP_PATH.read_text(encoding="utf-8")

    assert "_section_refresh_button(" not in source
    assert "Run Trading Agent" not in source
    assert "Run attention refresh job" not in source
    assert "Run equities refresh job" not in source
    assert "Run FRED refresh job" not in source
    assert "Run options refresh job" not in source
    assert "Load FRED Data" not in source
    assert "Analyze in Zopedia" not in source
    assert "key=f\"admin_run_job_{job_name}\"" in source
