"""Run or inspect the resumable historical corpus collection."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import plistlib
import re
import fcntl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def local_jobs(args):
    """Supervise resumable jobs for this macOS login, outside the chat process."""
    if sys.platform != "darwin":
        raise RuntimeError("Local job supervision requires macOS launchd")
    jobs = {"markets-news": ("markets,news", 8), "sec": ("sec", 3),
            "gdelt": ("gdelt", 4), "crypto": ("crypto_archive", 4),
            "social": ("social,social_full", 5), "release":
            ("macro,markets,news,public,social,social_full,gdelt,crypto_archive,sec", 1)}
    directory = args.state.resolve() / "launchd"
    directory.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    for name, (groups, workers) in jobs.items():
        label = f"com.spectralnature.historical-corpus.{name}"
        service = f"{domain}/{label}"
        current = subprocess.run(["launchctl", "print", service], capture_output=True, text=True)
        path = directory / f"{label}.plist"
        if args.local_jobs == "stop":
            if current.returncode == 0:
                subprocess.run(["launchctl", "bootout", service], check=True)
            print(json.dumps({"job": name, "state": "stopped"}), flush=True)
            continue
        if args.local_jobs == "start" and current.returncode != 0:
            if not args.training_catalog:
                raise ValueError("Local jobs require --training-catalog")
            argv = [str(ROOT / ".venv/bin/python"), "-u", str(Path(__file__).resolve()),
                "--spec", str(args.spec.resolve()), "--state", str(args.state.resolve()),
                "--groups", groups, "--workers", str(workers), "--storage-auth", args.storage_auth]
            if name == "release":
                argv += ["--watch-finalize", "--training-catalog", str(args.training_catalog.resolve())]
                if args.task_file:
                    argv += ["--task-file", str(args.task_file.resolve())]
            config = {"Label": label, "ProgramArguments": argv, "WorkingDirectory": str(ROOT),
                "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
                "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 300,
                "StandardOutPath": str(args.state.resolve() / f"supervised_{name}.log"),
                "StandardErrorPath": str(args.state.resolve() / f"supervised_{name}.log")}
            path.write_bytes(plistlib.dumps(config))
            subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
            current = subprocess.run(["launchctl", "print", service], capture_output=True, text=True)
        info = {key: value.strip() for key, value in re.findall(
            r"^\s*(state|pid|last exit code) = ([^\n]+)", current.stdout, flags=re.M)}
        print(json.dumps({"job": name, **(info or {"state": "not_loaded"})}), flush=True)


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    # Only the existing non-secret deployment coordinates are imported here.
    coordinates = {}
    for line in (ROOT / "infra/.generated/deployment.local.env").read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        coordinates[key] = value.strip().strip('"').strip("'")
        if key in {"AZURE_STORAGE_ACCOUNT_URL", "AZURE_STORAGE_CONTAINER", "KEYVAULT_NAME",
                   "KEY_VAULT_NAME", "AZURE_KEY_VAULT_NAME"}:
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=ROOT / "cache/historical_corpus")
    parser.add_argument("--groups", default="macro,markets,news,public,social,gdelt")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--publish-only", action="store_true")
    parser.add_argument("--storage-auth", choices=["runtime", "account-key"], default="runtime")
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--training-catalog", type=Path)
    parser.add_argument("--finalize-only", action="store_true")
    parser.add_argument("--watch-finalize", action="store_true")
    parser.add_argument("--local-jobs", choices=["start", "status", "stop"])
    args = parser.parse_args()
    if args.local_jobs:
        local_jobs(args)
        return
    specification = json.loads(args.spec.read_text())
    # Fail before authenticated source access if another launcher owns this
    # family. Task-level leases additionally protect overlapping supplements.
    leases = []
    if not args.publish_only:
        owners = ["training-release"] if args.finalize_only or args.watch_finalize else [
            "source-" + group for group in sorted(filter(None, args.groups.split(",")))]
        directory = args.state / "locks"
        directory.mkdir(parents=True, exist_ok=True)
        for owner in owners:
            lease = (directory / f"{owner}.lock").open("a")
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({"status": "already_running", "owner": owner}), flush=True)
                return
            leases.append(lease)
    os.environ["PIPELINE_DATASET_NAMESPACE"] = specification["namespace"]
    os.environ["APP_TRACK"] = "dev" if specification["namespace"] == "development" else specification["namespace"]
    if not os.environ.get("SEC_USER_AGENT"):
        contact = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, timeout=5).stdout.strip()
        if "@" in contact and not contact.endswith(("example.com", "users.noreply.github.com")):
            os.environ["SEC_USER_AGENT"] = f"SpectralNature research {contact}"
    if args.storage_auth == "account-key":
        # Operator identity already permits listing account keys. No role changes;
        # the credential remains in process memory and is never logged or saved.
        result = subprocess.run(["az", "storage", "account", "keys", "list",
            "--account-name", coordinates["STORAGE_ACCOUNT"], "--resource-group",
            coordinates["PIPELINE_RESOURCE_GROUP"], "--query", "[0].value", "-o", "tsv"],
            capture_output=True, text=True, timeout=30)
        if result.returncode or not result.stdout.strip():
            raise RuntimeError("Operator storage credential unavailable")
        os.environ["AZURE_STORAGE_ACCOUNT_KEY"] = result.stdout.strip()
    from services.historical_corpus import HistoricalCorpus
    corpus = HistoricalCorpus(specification, args.state)
    groups = set(filter(None, args.groups.split(",")))
    extra_tasks = json.loads(args.task_file.read_text()) if args.task_file else []
    if args.finalize_only or args.watch_finalize:
        if not args.training_catalog:
            parser.error("Finalization requires --training-catalog")
        from services.historical_training import publish_training_release
        while True:
            # Source catalogs and the explicit supplement can grow while the
            # independent collectors run; each release captures their exact set.
            extra_tasks = json.loads(args.task_file.read_text()) if args.task_file else []
            tasks = list({json.dumps(t, sort_keys=True): t for t in corpus.build_tasks(groups) + extra_tasks}.values())
            catalog = json.loads(args.training_catalog.read_text())
            summary = publish_training_release(corpus, tasks, catalog)
            print(json.dumps({"training_release": summary}), flush=True)
            if not args.watch_finalize or (not any(summary["task_counts"].get(s) for s in ["pending", "running"]) and not summary["derivation_counts"].get("pending") and not summary["canonicalization_counts"].get("pending")):
                break
            # Bounded sleeps keep interruption responsive. Each release is
            # immutable; the local receipt points to the newest frozen version.
            time.sleep(60)
    elif args.publish_only:
        corpus.publish(corpus.build_tasks(groups) + extra_tasks, label="-".join(sorted(groups)))
    else:
        corpus.run(groups=groups, workers=args.workers, extra_tasks=extra_tasks)


if __name__ == "__main__":
    main()
