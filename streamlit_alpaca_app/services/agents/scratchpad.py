"""
Agent scratchpad — persistent session-scoped store for research state.

The agent writes anomaly events, search queries, evidence claims, and hypothesis
drafts here.  Each entry is timestamped and keyed by run_id so multiple research
sessions can coexist.  The backing store is a JSON file in /tmp (ephemeral per
container) plus an optional session_state mirror for Streamlit UI access.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


_SCRATCHPAD_DIR = Path(os.environ.get("SCRATCHPAD_DIR", "/tmp/spectral_scratchpad"))


def _ensure_dir() -> Path:
    _SCRATCHPAD_DIR.mkdir(parents=True, exist_ok=True)
    return _SCRATCHPAD_DIR


def _path_for_run(run_id: str) -> Path:
    safe_id = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(run_id or "scratch"))
    return _ensure_dir() / f"{safe_id}.json"


def _load(run_id: str) -> dict[str, Any]:
    path = _path_for_run(run_id)
    if not path.exists():
        return {"run_id": run_id, "created_at": time.time(), "entries": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"run_id": run_id, "created_at": time.time(), "entries": []}


def _save(run_id: str, data: dict[str, Any]) -> None:
    path = _path_for_run(run_id)
    path.write_text(json.dumps(data, ensure_ascii=False, default=str, indent=2), encoding="utf-8")


def write_entry(
    *,
    run_id: str,
    kind: str,
    content: dict[str, Any],
) -> dict[str, Any]:
    """Append an entry to the scratchpad. Returns the entry with its index."""
    data = _load(run_id)
    entry = {
        "index": len(data["entries"]),
        "kind": str(kind or "note"),
        "timestamp": time.time(),
        "content": dict(content or {}),
    }
    data["entries"].append(entry)
    data["updated_at"] = time.time()
    _save(run_id, data)
    return entry


def read_entries(
    *,
    run_id: str,
    kind: str | None = None,
    last_n: int | None = None,
) -> list[dict[str, Any]]:
    """Read entries from the scratchpad, optionally filtered by kind."""
    data = _load(run_id)
    entries = list(data.get("entries") or [])
    if kind:
        entries = [e for e in entries if str(e.get("kind") or "") == kind]
    if last_n and last_n > 0:
        entries = entries[-last_n:]
    return entries


def read_summary(*, run_id: str) -> dict[str, Any]:
    """Return a compact summary of the scratchpad state."""
    data = _load(run_id)
    entries = list(data.get("entries") or [])
    kinds: dict[str, int] = {}
    for e in entries:
        k = str(e.get("kind") or "unknown")
        kinds[k] = kinds.get(k, 0) + 1
    latest_hypothesis = None
    for e in reversed(entries):
        if str(e.get("kind") or "") == "hypothesis":
            latest_hypothesis = e.get("content", {}).get("hypothesis_text", "")
            break
    return {
        "run_id": run_id,
        "entry_count": len(entries),
        "kinds": kinds,
        "latest_hypothesis": latest_hypothesis,
    }


def clear(*, run_id: str) -> None:
    """Remove a scratchpad file."""
    path = _path_for_run(run_id)
    if path.exists():
        path.unlink(missing_ok=True)


__all__ = [
    "clear",
    "read_entries",
    "read_summary",
    "write_entry",
]
