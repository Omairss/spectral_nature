#!/usr/bin/env python3
"""Prepare or explicitly deliver Attention posts through the owned X browser."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from services.attention_x import prepare_attention_posts, deliver_attention_posts
from services.x_publishing import XAccountStore, open_x_account, configured_x_account
from services.secrets import runtime_env_value


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("inspect","prepare","deliver","status"))
    parser.add_argument("--account",default=runtime_env_value("X_ACCOUNT_USERNAME"))
    parser.add_argument("--headed",action="store_true")
    parser.add_argument("--maximum",type=int,default=1)
    args=parser.parse_args()
    if not args.account:
        args.account = configured_x_account()
    if args.maximum<1:
        parser.error("--maximum must be positive")
    if args.action=="inspect":
        async def inspect():
            async with open_x_account(args.account,headless=not args.headed) as browser:
                print(json.dumps(await browser.inspect_composer(),indent=2),flush=True)
        asyncio.run(inspect())
        return
    if args.action=="status":
        print(json.dumps(XAccountStore(args.account).records(),indent=2),flush=True)
        return
    if args.action=="deliver":
        print(json.dumps(deliver_attention_posts(args.account,headless=not args.headed,maximum=args.maximum),indent=2),flush=True)
        return
    from services.attention_materialized import deserialize_attention_home_payload
    from services.pipeline_store import latest_job_execution_receipt, dataset_metadata_for_version, load_dataset_frame
    completion=latest_job_execution_receipt("attention-home-build")
    if not completion or completion.get("status") not in {"Succeeded","Warning"}:
        raise RuntimeError("No completed Attention run is available.")
    version=(completion.get("datasets") or {}).get("attention_home_snapshots_1d")
    metadata=dataset_metadata_for_version("attention_home_snapshots_1d",version) if version else None
    if metadata is None:
        raise RuntimeError("The completed Attention publication is unavailable.")
    result=prepare_attention_posts(deserialize_attention_home_payload(load_dataset_frame(metadata)),
                                   source_version=version,account=args.account)
    print(json.dumps(result,indent=2),flush=True)


if __name__=="__main__":
    main()
