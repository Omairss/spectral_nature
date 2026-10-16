#!/usr/bin/env python3
"""Verify accepted Git source before packaging any application consumer."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


APP = Path(__file__).resolve().parents[1]
MANIFEST = APP / "infra/release_acceptance.json"
# These trees are excluded by .dockerignore and cannot supply runtime code.
EXCLUDED = ("documents/", "infra/", "ios_app/", "tests/", "output/", "notebooks/")


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(APP.parent), *args], stderr=subprocess.PIPE)


def inventory(revision: str = "HEAD") -> dict[str, str]:
    paths = git("ls-tree", "-r", "--name-only", revision, "--", "streamlit_alpaca_app").decode().splitlines()
    result = {}
    for path in paths:
        name = path.removeprefix("streamlit_alpaca_app/")
        if name.startswith(EXCLUDED):
            continue
        result[name] = hashlib.sha256(git("show", f"{revision}:{path}")).hexdigest()
    return result


def verify_target(role: str, target: str) -> dict:
    accepted = json.loads(MANIFEST.read_text())
    contract = accepted.get("roles", {}).get(role)
    if accepted.get("status") != "accepted_locally" or contract is None:
        raise ValueError(f"No accepted consumer contract for {role}.")
    targets = contract.get("development_targets", []) + contract.get("production_targets", [])
    if target not in {item["name"] for item in targets}:
        raise ValueError(f"{target} is not an accepted target for {role}; review its actual current application role.")
    return {"role": role, "target": target}


def verify(role: str) -> dict:
    root = Path(git("rev-parse", "--show-toplevel").decode().strip())
    if APP != root / "streamlit_alpaca_app":
        raise ValueError("Release source must be an actual Git application checkout.")
    if git("status", "--porcelain", "--untracked-files=all", "--", "streamlit_alpaca_app").strip():
        raise ValueError("Commit the reviewed application source and its acceptance record before packaging.")
    accepted = json.loads(MANIFEST.read_text())
    if accepted.get("status") != "accepted_locally":
        raise ValueError("Application source has no local acceptance.")
    if role not in accepted.get("roles", {}):
        raise ValueError(f"No accepted source/dependency contract for {role}.")
    selected = inventory()
    recorded = accepted["files"]
    if not selected or not accepted.get("components"):
        raise ValueError("An empty inventory or component review cannot authorize a release.")
    differences = sorted(name for name in selected.keys() | recorded.keys() if selected.get(name) != recorded.get(name))
    if differences:
        raise ValueError("Unreviewed source changes: " + ", ".join(differences[:20]))
    if inventory(accepted["source_revision"]) != selected:
        raise ValueError("Accepted source revision does not match the selected application files.")
    changed = git("diff", "--name-only", accepted["review_base_revision"], accepted["source_revision"],
                  "--", "streamlit_alpaca_app").decode().splitlines()
    changed = {name.removeprefix("streamlit_alpaca_app/") for name in changed
               if not name.removeprefix("streamlit_alpaca_app/").startswith(EXCLUDED)}
    if changed != set(accepted["reviewed_changes"]) or not all(accepted["reviewed_changes"].values()):
        raise ValueError("Every changed application file needs a recorded review against the previous source.")
    retired = set(accepted.get("retired_files", []))
    if retired & selected.keys():
        raise ValueError("A retired application file is still present in the selected source.")
    for component in accepted["components"]:
        if component.get("status") != "accepted" or not component.get("evidence") or not component.get("files"):
            raise ValueError("Each component needs explicit acceptance and verification evidence.")
        if not (APP / component["evidence"]).is_file():
            raise ValueError("Missing component verification evidence.")
        if not set(component["files"]).issubset(selected.keys() | retired):
            raise ValueError("Component review references source outside the accepted inventory.")
    contract = accepted["roles"][role]
    if not contract.get("baseline_images") or "@sha256:" not in contract["dependency_image"]:
        raise ValueError("Pin the dependencies and each consumer's previous accepted image.")
    for name in contract["baseline_images"]:
        if "@sha256:" not in name:
            raise ValueError("Every consumer baseline must identify an immutable image.")
    fingerprint = hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"source_revision": git("rev-parse", "HEAD").decode().strip(),
            "accepted_source_revision": accepted["source_revision"], "role": role,
            "source_inventory_sha256": fingerprint, "files": len(selected),
            "dependency_image": accepted["roles"][role]["dependency_image"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=("worker", "api", "api-next", "streamlit", "broker", "web"))
    parser.add_argument("--target")
    parser.add_argument("--target-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.target_only and not args.target:
            raise ValueError("Target-only checks require a target name.")
        if args.target:
            verify_target(args.role, args.target)
        print(json.dumps(verify_target(args.role, args.target) if args.target_only else verify(args.role), sort_keys=True))
    except (ValueError, KeyError, FileNotFoundError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Release source rejected: {exc}\n")


if __name__ == "__main__":
    main()
