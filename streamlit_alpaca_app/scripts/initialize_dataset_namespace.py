#!/usr/bin/env python3
"""Initialize one materialized-dataset namespace from another namespace's latest snapshots."""

from pathlib import Path
import sys


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from pipeline.jobs.namespace_refresh import main  # noqa: E402


if __name__ == "__main__":
    main()
