#!/usr/bin/env python3
"""Build accepted Git source and update existing development image templates.

No source extraction, application overlay, environment rewrite, job execution,
schema migration or production mutation is performed by this entrypoint.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from release_source import APP, acceptance, verify, verify_image

API_VERSION = "2025-07-01"


def azure(*args: str):
    result = subprocess.run(["az", *args, "--only-show-errors", "-o", "json"],
                            capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(f"Azure operation failed: {' '.join(args[:3])}; inspect the retained operator receipt.")
    return json.loads(result.stdout) if result.stdout.strip() else None


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")
    path.chmod(0o600)


def configuration(resource: dict) -> dict:
    value = {key: deepcopy(resource.get(key)) for key in ("identity", "location")}
    value["configuration"] = deepcopy(resource["properties"]["configuration"])
    value["template"] = deepcopy(resource["properties"]["template"])
    value["template"]["containers"][0].pop("image", None)
    return value


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build(args) -> None:
    release = verify(args.role)
    tag = f"accepted-{args.role}:{release['source_revision'][:12]}"
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    save(args.output / "source.json", release)
    run = azure("acr", "build", "--registry", args.registry, "--resource-group", args.resource_group,
                "--image", tag, "--file", str(APP / "Dockerfile.runtime"), "--target", args.role,
                "--build-arg", f"DEPENDENCY_IMAGE={release['dependency_image']}",
                "--build-arg", f"SOURCE_REVISION={release['source_revision']}",
                "--build-arg", f"SOURCE_INVENTORY_SHA256={release['source_inventory_sha256']}",
                "--build-arg", f"RUNTIME_INVENTORY_SHA256={release['runtime_inventory_sha256']}",
                "--no-wait", "--no-logs", str(APP))
    save(args.output / "build.json", {**release, "tag": tag, "run": run})
    print(json.dumps({"role": args.role, "tag": tag,
                      "run_id": (run or {}).get("runId"), "status": (run or {}).get("status")}))


def deploy(args) -> None:
    record = json.loads(args.images.read_text())
    accepted = acceptance()
    receipts = []
    # Recheck the complete source and each role before making the first change.
    for role in record["images"]:
        source = verify(role)
        if source["source_inventory_sha256"] != record["source_inventory_sha256"]:
            raise ValueError("Images do not belong to the selected accepted revision.")
        if "@sha256:" not in record["images"][role]:
            raise ValueError("Image templates require immutable digests.")
        verify_image(role, record["images"][role])
    for role, image in record["images"].items():
        for target in accepted["roles"][role].get("development_targets", []):
            kind, name = target["kind"], target["name"]
            path = f"/subscriptions/{args.subscription}/resourceGroups/{args.resource_group}/providers/Microsoft.App/{kind}/{name}"
            url = f"https://management.azure.com{path}?api-version={API_VERSION}"
            before = azure("rest", "--method", "get", "--url", url)
            save(args.output / f"{name}.before.json", before)
            template = deepcopy(before["properties"]["template"])
            if len(template["containers"]) != 1:
                raise ValueError(f"Review multiple application containers before changing {name}.")
            previous_image = template["containers"][0]["image"]
            if previous_image not in {target["previous_image"], image}:
                raise ValueError(f"{name} no longer has its reviewed prior image; reconcile that consumer before release.")
            if fingerprint(configuration(before)) != target["configuration_sha256"]:
                raise ValueError(f"{name} configuration changed after review; preserve and reconcile that change first.")
            template["containers"][0]["image"] = image
            body = args.output / f"{name}.patch.json"
            save(body, {"properties": {"template": template}})
            options = ["--headers", f"If-Match={before['etag']}"] if before.get("etag") else []
            # Read again to avoid replacing a template changed during preparation.
            fresh = azure("rest", "--method", "get", "--url", url)
            if fresh["properties"]["template"] != before["properties"]["template"]:
                raise ValueError(f"{name} changed during release preparation.")
            azure("rest", "--method", "patch", "--url", url, "--body", "@" + str(body), *options)
            after = azure("rest", "--method", "get", "--url", url)
            save(args.output / f"{name}.after.json", after)
            if configuration(before) != configuration(after):
                raise ValueError(f"Configuration changed beyond the image on {name}; inspect the saved definitions.")
            if after["properties"]["template"]["containers"][0]["image"] != image:
                raise ValueError(f"Image update was not retained on {name}.")
            receipt = {"name": name, "kind": kind, "role": role, "previous_image": previous_image,
                       "image": image, "configuration_sha256": fingerprint(configuration(before)),
                       "provisioning_state": after["properties"].get("provisioningState")}
            receipts.append(receipt)
            save(args.output / "deployment.json", {"environment": "development", **record, "targets": receipts,
                 "recorded_at": datetime.now(timezone.utc).isoformat(), "job_executions_started": 0})
            print(json.dumps(receipt), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", default="sn-pipeline-rg-03130136")
    parser.add_argument("--registry", default="snpipelineacr03130136")
    commands = parser.add_subparsers(dest="command", required=True)
    package = commands.add_parser("build")
    package.add_argument("--role", required=True, choices=("worker", "api-next", "broker"))
    package.add_argument("--output", type=Path, required=True)
    development = commands.add_parser("deploy-development")
    development.add_argument("--subscription", required=True)
    development.add_argument("--images", type=Path, required=True)
    development.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args) if args.command == "build" else deploy(args)


if __name__ == "__main__":
    main()
