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
    (app / "main.py").write_text("accepted = True\n")
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
              "files": release.inventory(), "roles": {"worker": {"dependency_image": image, "baseline_images": [image],
                  "development_targets": [{"name": "worker-dev"}]}},
              "components": [{"status": "accepted", "files": ["main.py"], "evidence": "documents/verification.md"}]}

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
    assert release.verify("worker")["files"] == 1


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
    (release.APP / "main.py").write_text("accepted = False\n")
    record["source_revision"] = commit()
    record["files"] = release.inventory()
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
