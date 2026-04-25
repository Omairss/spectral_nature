from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services"
PIPELINE_ROOT = ROOT / "pipeline"


FORBIDDEN_PATTERNS = {
    "AQL chat log implementation imports must go through services.agents": (
        "from .aql.chat_log",
        "from services.aql.chat_log",
    ),
    "AQL scratchpad implementation imports must go through services.agents": (
        "from .aql.scratchpad",
        "from services.aql.scratchpad",
    ),
    "AQL hypothesis verification imports must go through services.common or services.agents": (
        "from .aql.summarizer import verify_hypothesis",
        "from services.aql.summarizer import verify_hypothesis",
    ),
    "Shared market activity helpers must go through services.common": (
        "from .market_activity_shared",
        "from services.market_activity_shared",
    ),
    "Anomaly detection imports must go through services.market_data": (
        "from compute.anomalies",
    ),
}

ALLOWED_FILES = {
    SERVICE_ROOT / "aql" / "chat_log.py",
    SERVICE_ROOT / "aql" / "scratchpad.py",
    SERVICE_ROOT / "market_activity_shared.py",
    SERVICE_ROOT / "market_data" / "__init__.py",
}


def _python_files() -> list[Path]:
    roots = [SERVICE_ROOT, PIPELINE_ROOT]
    files: list[Path] = []
    for root in roots:
        files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return sorted(files)


def test_service_module_boundaries_do_not_import_legacy_internals():
    violations: list[str] = []
    for path in _python_files():
        if path in ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for reason, patterns in FORBIDDEN_PATTERNS.items():
            for pattern in patterns:
                if pattern in text:
                    violations.append(f"{path.relative_to(ROOT)}: {reason}: {pattern}")

    assert not violations, "\n".join(violations)
