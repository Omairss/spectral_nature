"""Release provenance contracts; these do not judge generated language."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("release_source", Path(__file__).parents[1] / "scripts/release_source.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    app = tmp_path / "streamlit_alpaca_app"
    (app / "infra").mkdir(parents=True)
    (app / "documents").mkdir()
    (app / "infra/retired_paths.json").write_text(json.dumps({"paths": ["src", "streamlit_alpaca_app/app.py"]}))
    (app / "api").mkdir()
    (app / "api/main.py").write_text("accepted = True\n")
    (app / "config").mkdir()
    (app / "config/release_contracts.json").write_text(json.dumps({"runtime": [
        {"name": "shared_runtime", "roles": ["worker"], "files": ["api/main.py"]}]}))
    (app / "documents/verification.md").write_text("Reviewed complete behavior.\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(release, "APP", app)
    monkeypatch.setattr(release, "MANIFEST", app / "infra/release_acceptance.json")

    def commit():
        release.git("add", ".")
        release.git("-c", "user.name=Release Check", "-c", "user.email=check@example.invalid", "commit", "-qm", "fixture")
        return release.git("rev-parse", "HEAD").decode().strip()

    revision = commit()
    image = "registry.example/runtime@sha256:" + "a" * 64
    record = {"status": "accepted_locally", "source_revision": revision,
              "review_base_revision": revision, "reviewed_changes": {},
              "files": release.inventory(), "runtime_files": release.runtime_inventory(release.inventory()),
              "roles": {"worker": {"dependency_image": image, "baseline_images": [image],
                  "development_targets": [{"name": "worker-dev", "previous_image": image,
                      "preservation_review": {"status": "accepted", "image": image,
                                              "evidence": "documents/verification.md"}}]}},
              "consumer_contract_reviews": {"shared_runtime": {"status": "accepted",
                  "evidence": "documents/verification.md"}},
              "components": [{"status": "accepted", "files": ["api/main.py"], "evidence": "documents/verification.md"}]}

    def save():
        release.MANIFEST.write_text(json.dumps(record))
        commit()

    return release, record, commit, save


def test_clean_checkpoint_without_acceptance_is_rejected(checkout):
    release, _, _, _ = checkout
    with pytest.raises(FileNotFoundError):
        release.verify("worker")


def test_complete_accepted_inventory_is_required(checkout):
    release, _, _, save = checkout
    save()
    assert release.verify("worker")["files"] == 2


def test_committed_unreviewed_source_cannot_build(checkout):
    release, _, commit, save = checkout
    save()
    (release.APP / "another_producer.py").write_text("older_behavior = True\n")
    commit()
    with pytest.raises(ValueError, match="Unreviewed source changes"):
        release.verify("worker")


def test_updating_hashes_alone_does_not_accept_a_changed_file(checkout):
    release, record, commit, save = checkout
    save()
    (release.APP / "api/main.py").write_text("accepted = False\n")
    record["source_revision"] = commit()
    record["files"] = release.inventory()
    record["runtime_files"] = release.runtime_inventory(record["files"])
    save()
    with pytest.raises(ValueError, match="Every changed application file"):
        release.verify("worker")


def test_an_accepted_role_cannot_replace_a_different_application(checkout):
    release, _, _, save = checkout
    save()
    assert release.verify_target("worker", "worker-dev")["target"] == "worker-dev"
    with pytest.raises(ValueError, match="not an accepted target"):
        release.verify_target("worker", "sn-streamlit-ui")


def test_old_or_unverified_images_cannot_be_promoted(checkout):
    release, _, _, save = checkout
    save()
    with pytest.raises(ValueError, match="Image is not verified"):
        release.verify_image("worker", "registry.example/older@sha256:" + "b" * 64)


def test_retired_source_is_rejected_even_outside_application_inventory(checkout):
    release, _, _, save = checkout
    save()
    (release.APP.parent / "src").mkdir()
    with pytest.raises(ValueError, match="Retired paths restored"):
        release.verify("worker")


def test_retired_role_cannot_be_authorized_by_a_manifest(checkout):
    release, record, _, save = checkout
    record["roles"]["streamlit"] = record["roles"]["worker"]
    save()
    with pytest.raises(ValueError, match="Retired or unsupported"):
        release.verify("streamlit")


def test_web_role_uses_complete_frontend_inventory(checkout):
    import hashlib
    release, record, commit, save = checkout
    (release.APP / "web").mkdir()
    (release.APP / "web/page.tsx").write_text("export default function Page() { return null; }\n")
    revision = commit()
    record.update(source_revision=revision, review_base_revision=revision, files=release.inventory())
    record["roles"]["web"] = {**record["roles"]["worker"], "runtime_files": {
        name: digest for name, digest in record["files"].items() if name.startswith("web/")}}
    record["roles"]["web"]["development_targets"] = [
        {**record["roles"]["worker"]["development_targets"][0], "name": "web-dev"}]
    save()
    checked = release.verify("web")
    assert checked["runtime_files"] == 1
    expected = hashlib.sha256(json.dumps(record["roles"]["web"]["runtime_files"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert checked["runtime_inventory_sha256"] == expected
    record["roles"]["web"]["runtime_files"] = {}
    save()
    with pytest.raises(ValueError, match="complete reviewed frontend"):
        release.verify("web")


def test_retained_consumer_requires_review_even_when_its_image_is_unchanged(checkout):
    release, record, _, save = checkout
    record["untouched_consumers"] = [{"name": "retained-worker", "role": "worker", "image": "old"}]
    save()
    with pytest.raises(ValueError, match="explicit behavior review"):
        release.verify("worker")


def test_verified_old_image_cannot_silently_miss_a_new_shared_contract(checkout):
    release, record, commit, save = checkout
    old_revision = record["source_revision"]
    old_image = record["roles"]["worker"]["dependency_image"]
    proof = release.APP / "documents/package.json"
    proof.write_text(json.dumps([{"role": "worker", "image": old_image,
        "source_revision": old_revision, "all_packaged_files_checked": True}]))
    record["untouched_consumers"] = [{"name": "retained-worker", "role": "worker", "image": old_image,
        "preservation_review": {"status": "accepted", "image": old_image,
            "source_revision": old_revision, "package_evidence": "documents/package.json",
            "evidence": "documents/verification.md"}}]
    (release.APP / "api/main.py").write_text("accepted = False\n")
    record.update(source_revision=commit(), files=release.inventory(),
                  reviewed_changes={"api/main.py": "Reviewed changed shared contract."})
    record["runtime_files"] = release.runtime_inventory(record["files"])
    save()
    with pytest.raises(ValueError, match="lacks accepted shared_runtime"):
        release.verify("worker")


def test_compatible_retained_source_still_requires_the_actual_image_proof(checkout):
    release, record, _, save = checkout
    image = record["roles"]["worker"]["dependency_image"]
    proof = release.APP / "documents/package.json"
    proof.write_text(json.dumps([{"role": "worker", "image": "different",
        "source_revision": record["source_revision"], "all_packaged_files_checked": True}]))
    record["untouched_consumers"] = [{"name": "retained-worker", "role": "worker", "image": image,
        "preservation_review": {"status": "accepted", "image": image,
            "source_revision": record["source_revision"], "package_evidence": "documents/package.json",
            "evidence": "documents/verification.md"}}]
    save()
    with pytest.raises(ValueError, match="does not cover retained consumer"):
        release.verify("worker")


def test_production_recovery_binding_cannot_be_omitted_from_consumer_review(checkout):
    release, record, commit, _ = checkout
    path = release.APP / "config/release_contracts.json"
    policy = json.loads(path.read_text())
    required = {"name": "recovery", "role": "worker", "pipeline_job": "system-recovery",
                "dataset_namespace": "", "trigger": "Schedule", "cron": "7,22,37,52 * * * *"}
    policy["production_jobs"] = [required]
    path.write_text(json.dumps(policy))
    commit()
    with pytest.raises(ValueError, match="production execution binding is missing"):
        release.verify_consumer_contracts(record)
    image = record["roles"]["worker"]["dependency_image"]
    record["roles"]["worker"]["production_targets"] = [{"name": "recovery", "kind": "jobs",
        "new_resource": True, "baseline_image": image, "execution_binding": required,
        "preservation_review": {"status": "accepted", "image": image,
                                "evidence": "documents/verification.md"}}]
    release.verify_consumer_contracts(record)
