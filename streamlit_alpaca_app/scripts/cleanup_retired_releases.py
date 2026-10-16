#!/usr/bin/env python3
"""Discard completed release-source archives after their 30-day retention."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil


ARCHIVE_ROOT = Path.home() / "Library/Application Support/SpectralNature/retired-release-sources"
BATCH_NAME = re.compile(r"\d{8}T\d{6}Z")


def purge_expired(root: Path = ARCHIVE_ROOT, *, now: datetime | None = None) -> list[str]:
    """Remove only owned, completed batches whose full retention has elapsed."""
    if root.is_symlink():
        raise ValueError("The archive root must not be a symlink.")
    if not root.exists():
        return []
    now = now or datetime.now(timezone.utc)
    removed = []
    for batch in sorted(root.iterdir()):
        if batch.is_symlink() or not batch.is_dir() or not BATCH_NAME.fullmatch(batch.name):
            continue
        manifest = batch / "manifest.json"
        if manifest.is_symlink():
            continue
        try:
            record = json.loads(manifest.read_text())
            if not isinstance(record, dict):
                continue
            if record.get("kind") != "spectral_nature_retired_release_sources" or record.get("status") != "complete":
                continue
            completed = datetime.fromisoformat(record["completed_at"])
            expires = datetime.fromisoformat(record["expires_at"])
            if completed.tzinfo is None or expires.tzinfo is None:
                continue
            # A damaged or edited expiry cannot shorten the owner's retention.
            if now < max(expires, completed + timedelta(days=30)):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        shutil.rmtree(batch)
        removed.append(batch.name)
    return removed


if __name__ == "__main__":
    for name in purge_expired():
        print(f"Removed expired release-source archive {name}")
