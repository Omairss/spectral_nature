#!/usr/bin/env python3
"""Verify accepted Git source before packaging any application consumer."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess


APP = Path(__file__).resolve().parents[1]
MANIFEST = APP / "infra/release_acceptance.json"
# These trees are excluded by .dockerignore and cannot supply runtime code.
EXCLUDED = ("documents/", "infra/", "ios_app/", "tests/", "output/", "notebooks/")
SUPPORTED_ROLES = ("worker", "api-next", "broker", "web")
RUNTIME_ROOTS = ("api/", "compute/", "config/", "data/", "data_access/", "pipeline/",
                 "presentation/", "services/", "branding/")
RUNTIME_FILES = {"cache/cache_policy.json", "scripts/sync_robinhood_main.py",
                 "scripts/collect_historical_corpus.py"}


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


def check_retirement(role: str) -> None:
    if role not in SUPPORTED_ROLES:
        raise ValueError(f"Retired or unsupported application role: {role}.")
    policy = json.loads(git("show", "HEAD:streamlit_alpaca_app/infra/retired_paths.json"))
    root = APP.parent
    restored = [name for name in policy["paths"] if (root / name).exists() or (root / name).is_symlink()]
    if restored:
        raise ValueError("Retired paths restored in active checkout: " + ", ".join(restored[:10]))


def acceptance() -> dict:
    if not MANIFEST.is_file():
        raise FileNotFoundError("Missing committed release acceptance record.")
    return json.loads(git("show", "HEAD:streamlit_alpaca_app/infra/release_acceptance.json"))


def runtime_inventory(files: dict[str, str]) -> dict[str, str]:
    return {name: digest for name, digest in files.items() if name.startswith(RUNTIME_ROOTS) or name in RUNTIME_FILES}


def role_runtime_inventory(accepted: dict, role: str) -> dict[str, str]:
    if role == "web":
        selected = {name: digest for name, digest in accepted["files"].items() if name.startswith("web/")}
        if not selected or accepted["roles"][role].get("runtime_files") != selected:
            raise ValueError("The web role requires its complete reviewed frontend input inventory.")
        return selected
    return accepted["runtime_files"]


def declared_consumers(accepted: dict) -> dict[str, dict]:
    consumers = {}
    for role, contract in accepted["roles"].items():
        if role not in SUPPORTED_ROLES:
            raise ValueError(f"Unsupported consumer role: {role}.")
        for environment in ("development", "production"):
            for target in contract.get(environment + "_targets", []):
                name = target["name"]
                if name in consumers:
                    raise ValueError(f"Consumer declared more than once: {name}.")
                consumers[name] = {**target, "role": role, "delivery": "release",
                                   "environment": environment}
    for retained in accepted.get("untouched_consumers", []):
        name = retained["name"]
        if name in consumers or retained.get("role") not in accepted["roles"]:
            raise ValueError(f"Retained consumer needs one explicit application role: {name}.")
        consumers[name] = {**retained, "delivery": "retained"}
    return consumers


def _contract_signature(revision: str, contract: dict) -> dict[str, str]:
    signature = {}
    for path in contract.get("files", []):
        source = git("show", f"{revision}:streamlit_alpaca_app/{path}")
        value = ast.dump(ast.parse(source), include_attributes=False) if path.endswith(".py") else source.decode()
        signature["file:" + path] = hashlib.sha256(value.encode()).hexdigest()
    for path, names in contract.get("definitions", {}).items():
        source = ast.parse(git("show", f"{revision}:streamlit_alpaca_app/{path}"))
        definitions = {node.name: node for node in source.body if hasattr(node, "name")}
        for name in names:
            if name not in definitions:
                raise ValueError(f"Required consumer definition is missing: {path}:{name}.")
            value = ast.dump(definitions[name], include_attributes=False)
            signature[f"definition:{path}:{name}"] = hashlib.sha256(value.encode()).hexdigest()
    if not signature:
        raise ValueError("A consumer contract needs executable source requirements.")
    return signature


def verify_consumer_contracts(accepted: dict) -> None:
    consumers = declared_consumers(accepted)
    policy = json.loads(git("show", "HEAD:streamlit_alpaca_app/config/release_contracts.json"))
    contracts = policy.get("runtime")
    if not consumers or not contracts:
        raise ValueError("Complete consumer coverage and shared runtime contracts are required.")
    if len({contract.get("name") for contract in contracts}) != len(contracts):
        raise ValueError("Shared runtime contracts must have distinct names.")
    signatures = {}
    for contract in contracts:
        review = accepted.get("consumer_contract_reviews", {}).get(contract.get("name"), {})
        if (not contract.get("name") or not contract.get("roles") or
                not set(contract["roles"]).issubset(accepted["roles"]) or
                review.get("status") != "accepted" or
                not (APP / review.get("evidence", "")).is_file()):
            raise ValueError("Each shared runtime contract needs roles and review evidence.")
        paths = set(contract.get("files", [])) | set(contract.get("definitions", {}))
        if not paths.issubset(accepted["runtime_files"]):
            raise ValueError("Consumer requirements must belong to the packaged runtime.")
        signatures[contract["name"]] = _contract_signature(accepted["source_revision"], contract)
    for name, consumer in consumers.items():
        review = consumer.get("preservation_review", {})
        image = consumer.get("image") or consumer.get("previous_image") or consumer.get("baseline_image")
        if (review.get("status") != "accepted" or review.get("image") != image or
                not (APP / review.get("evidence", "")).is_file()):
            raise ValueError(f"Consumer needs an explicit behavior review: {name}.")
        if consumer["delivery"] == "release":
            continue  # Complete role image verification covers the selected source.
        applicable = [contract for contract in contracts if consumer["role"] in contract["roles"]]
        if not applicable:
            continue
        revision = review.get("source_revision")
        evidence = APP / review.get("package_evidence", "")
        if not revision or not evidence.is_file():
            raise ValueError(f"Retained consumer needs verified image/source evidence: {name}.")
        proofs = json.loads(evidence.read_text())
        if not isinstance(proofs, list) or not any(
            proof.get("image") == image and proof.get("role") == consumer["role"] and
            proof.get("source_revision") == revision and
            (proof.get("complete_git_inventory_matches") is True or
             proof.get("all_packaged_files_checked") is True)
            for proof in proofs
        ):
            raise ValueError(f"Image/source verification does not cover retained consumer: {name}.")
        for contract in applicable:
            key = (revision, contract["name"])
            if key not in signatures:
                try:
                    signatures[key] = _contract_signature(revision, contract)
                except subprocess.CalledProcessError as exc:
                    raise ValueError(f"Retained consumer lacks {contract['name']}: {name}.") from exc
            if signatures[key] != signatures[contract["name"]]:
                raise ValueError(f"Retained consumer lacks accepted {contract['name']}: {name}.")
    for required in policy.get("production_jobs", []):
        consumer = consumers.get(required["name"], {})
        if (consumer.get("role") != required["role"] or consumer.get("kind") != "jobs" or
                consumer.get("environment") != "production" or
                consumer.get("execution_binding") != required):
            raise ValueError(f"Required production execution binding is missing: {required['name']}.")


def verify_target(role: str, target: str) -> dict:
    check_retirement(role)
    accepted = acceptance()
    contract = accepted.get("roles", {}).get(role)
    if accepted.get("status") != "accepted_locally" or contract is None:
        raise ValueError(f"No accepted consumer contract for {role}.")
    targets = contract.get("development_targets", []) + contract.get("production_targets", [])
    if target not in {item["name"] for item in targets}:
        raise ValueError(f"{target} is not an accepted target for {role}; review its actual current application role.")
    return {"role": role, "target": target}


def verify_image(role: str, image: str) -> dict:
    check_retirement(role)
    accepted = acceptance()
    record = accepted.get("roles", {}).get(role, {}).get("accepted_images", {}).get(image)
    fingerprint = hashlib.sha256(json.dumps(accepted["files"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    runtime = hashlib.sha256(json.dumps(role_runtime_inventory(accepted, role), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not record or record.get("source_inventory_sha256") != fingerprint or record.get("runtime_inventory_sha256") != runtime:
        raise ValueError("Image is not verified for this accepted source and role. Historical rollback requires its separate explicit procedure.")
    return {"role": role, "image": image, **record}


def verify(role: str) -> dict:
    check_retirement(role)
    root = Path(git("rev-parse", "--show-toplevel").decode().strip())
    if APP != root / "streamlit_alpaca_app":
        raise ValueError("Release source must be an actual Git application checkout.")
    if git("status", "--porcelain", "--untracked-files=all", "--", "streamlit_alpaca_app").strip():
        raise ValueError("Commit the reviewed application source and its acceptance record before packaging.")
    accepted = acceptance()
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
    runtime = runtime_inventory(selected)
    if not runtime or accepted.get("runtime_files") != runtime:
        raise ValueError("Declared runtime files do not match the accepted source.")
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
    verify_consumer_contracts(accepted)
    contract = accepted["roles"][role]
    if not contract.get("baseline_images") or "@sha256:" not in contract["dependency_image"]:
        raise ValueError("Pin the dependencies and each consumer's previous accepted image.")
    for name in contract["baseline_images"]:
        if "@sha256:" not in name:
            raise ValueError("Every consumer baseline must identify an immutable image.")
    runtime = role_runtime_inventory(accepted, role)
    fingerprint = hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"source_revision": git("rev-parse", "HEAD").decode().strip(),
            "accepted_source_revision": accepted["source_revision"], "role": role,
            "source_inventory_sha256": fingerprint, "files": len(selected),
            "runtime_inventory_sha256": hashlib.sha256(json.dumps(runtime, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "runtime_files": len(runtime),
            "dependency_image": accepted["roles"][role]["dependency_image"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=SUPPORTED_ROLES)
    parser.add_argument("--target")
    parser.add_argument("--image-ref")
    parser.add_argument("--target-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.target_only and not args.target:
            raise ValueError("Target-only checks require a target name.")
        if args.target:
            verify_target(args.role, args.target)
        if args.image_ref:
            verify_image(args.role, args.image_ref)
        print(json.dumps(verify_target(args.role, args.target) if args.target_only else verify(args.role), sort_keys=True))
    except (ValueError, KeyError, FileNotFoundError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Release source rejected: {exc}\n")


if __name__ == "__main__":
    main()
