#!/usr/bin/env python3
"""Prepare or explicitly deliver Attention posts through the owned X browser."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from services.attention_x import prepare_attention_posts, deliver_attention_posts, pending_attention_publications
from services.x_publishing import XAccountStore, open_x_account
from services.secrets import runtime_env_value


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("inspect","prepare","deliver","watch","status"))
    parser.add_argument("--account",default=runtime_env_value("X_ACCOUNT_USERNAME"))
    parser.add_argument("--headed",action="store_true")
    parser.add_argument("--maximum",type=int,default=1)
    parser.add_argument("--publish",action="store_true",help="Enable real posts in watch mode; otherwise only prepare.")
    parser.add_argument("--poll-seconds",type=int,default=60)
    args=parser.parse_args()
    if not args.account:
        parser.error("--account or X_ACCOUNT_USERNAME is required")
    if args.maximum<1:
        parser.error("--maximum must be positive")
    if args.action=="inspect":
        with open_x_account(args.account,headless=not args.headed) as browser:
            print(json.dumps(browser.inspect_composer(),indent=2),flush=True)
        return
    if args.action=="status":
        print(json.dumps(XAccountStore(args.account).records(),indent=2),flush=True)
        return
    if args.action=="deliver":
        print(json.dumps(deliver_attention_posts(args.account,headless=not args.headed,maximum=args.maximum),indent=2),flush=True)
        return
    from services.attention_materialized import deserialize_attention_home_payload
    from services.pipeline_store import latest_dataset_metadata, load_dataset_frame
    while True:
        metadata=latest_dataset_metadata("attention_home_snapshots_1d")
        if metadata is None:
            raise RuntimeError("No completed Attention publication is available.")
        publications = [metadata] if args.action=="prepare" else pending_attention_publications(args.account,metadata)
        for publication in publications:
            frame=load_dataset_frame(publication)
            if frame.empty:
                raise RuntimeError("The retained Attention publication could not be read.")
            result=prepare_attention_posts(deserialize_attention_home_payload(frame),
                                           source_version=publication.dataset_version_id,account=args.account)
            print(json.dumps(result,indent=2),flush=True)
        if args.action=="prepare":
            return
        if args.publish:
            print(json.dumps(deliver_attention_posts(args.account,headless=not args.headed,maximum=args.maximum),indent=2),flush=True)
        time.sleep(max(args.poll_seconds,60))


if __name__=="__main__":
    main()
