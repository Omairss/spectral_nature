#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import robinhood_main, robinhood_main_store, robinhood_main_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize queued Robinhood main-account reads.")
    parser.add_argument("--environment", default=robinhood_main.environment_name())
    parser.add_argument("--schedule-due", action="store_true")
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()

    if args.migrate and not robinhood_main_store.ensure_schema():
        raise SystemExit("Robinhood local schema migration failed.")
    if not robinhood_main_store.schema_ready():
        raise SystemExit("Robinhood local schema is not installed; run with --migrate first.")

    enqueued = (
        robinhood_main_sync.enqueue_due(environment=args.environment)
        if args.schedule_due
        else 0
    )
    results = robinhood_main_sync.run_pending(
        environment=args.environment,
        limit=args.limit,
    )
    print(json.dumps({"enqueued": enqueued, "processed": len(results), "results": results}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
