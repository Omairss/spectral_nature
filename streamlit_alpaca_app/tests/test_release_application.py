"""Image promotion authorization and all-consumer preflight contracts."""
import importlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest


@pytest.fixture
def release(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    return importlib.import_module("release_application")


def test_conditional_permission_does_not_skip_development_acceptance(release, tmp_path, monkeypatch):
    images = tmp_path / "images.json"
    images.write_text(json.dumps({"images": {"worker": "registry@sha256:abc"}}))
    monkeypatch.setattr(release, "acceptance", lambda: {"production_authorized": False})
    monkeypatch.setattr(release, "azure", lambda *args: pytest.fail("Azure must not be called"))
    with pytest.raises(ValueError, match="development acceptance"):
        release.deploy(SimpleNamespace(images=images, command="deploy-production"))


def test_late_consumer_change_prevents_every_patch(release, tmp_path, monkeypatch):
    image = "registry@sha256:abc"
    resource = {"identity": {}, "location": "westus", "properties": {
        "configuration": {}, "template": {"containers": [{"image": "previous"}]}}}
    targets = [{"kind": "jobs", "name": name, "previous_image": "previous",
                "configuration_api_version": release.API_VERSION,
                "configuration_sha256": release.fingerprint(release.configuration(resource))}
               for name in ("first-dev", "last-dev")]
    record = {"images": {"worker": image}, "source_inventory_sha256": "reviewed"}
    images = tmp_path / "images.json"
    images.write_text(json.dumps(record))
    monkeypatch.setattr(release, "acceptance", lambda: {"roles": {"worker": {"development_targets": targets}}})
    monkeypatch.setattr(release, "verify", lambda role: {"source_inventory_sha256": "reviewed"})
    monkeypatch.setattr(release, "verify_image", lambda *args: None)

    def azure(*args):
        assert "patch" not in args, "A late preflight failure must prevent every mutation"
        if "/jobs?" in args[-1]:
            return {"value": [{**deepcopy(resource), "name": name} for name in ("first-dev", "last-dev")]}
        if "/containerApps?" in args[-1]:
            return {"value": []}
        value = deepcopy(resource)
        if "last-dev" in args[-1]:
            value["properties"]["configuration"]["changed"] = True
        return value

    monkeypatch.setattr(release, "azure", azure)
    with pytest.raises(ValueError, match="configuration changed"):
        release.deploy(SimpleNamespace(images=images, command="deploy-development", output=tmp_path,
                                       subscription="subscription", resource_group="group"))


def test_asynchronous_template_visibility_is_awaited(release, tmp_path, monkeypatch):
    image = "registry@sha256:abc"
    resource = {"identity": {}, "location": "westus", "properties": {
        "configuration": {}, "provisioningState": "Succeeded",
        "template": {"containers": [{"image": "previous"}]}}}
    target = {"kind": "jobs", "name": "worker-dev", "previous_image": "previous",
              "configuration_api_version": release.API_VERSION,
              "configuration_sha256": release.fingerprint(release.configuration(resource))}
    record = {"images": {"worker": image}, "source_inventory_sha256": "reviewed"}
    images = tmp_path / "images.json"
    images.write_text(json.dumps(record))
    monkeypatch.setattr(release, "acceptance", lambda: {"roles": {"worker": {"development_targets": [target]}}})
    monkeypatch.setattr(release, "verify", lambda role: {"source_inventory_sha256": "reviewed"})
    monkeypatch.setattr(release, "verify_image", lambda *args: None)
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)
    reads = []
    patches = []

    def azure(*args):
        value = deepcopy(resource)
        if "/jobs?" in args[-1]:
            return {"value": [{**value, "name": "worker-dev"}]}
        if "/containerApps?" in args[-1]:
            return {"value": []}
        if "patch" in args:
            patches.append(True)
        else:
            reads.append(True)
            if patches and len(reads) >= 4:
                value["properties"]["template"]["containers"][0]["image"] = image
        return value

    monkeypatch.setattr(release, "azure", azure)
    release.deploy(SimpleNamespace(images=images, command="deploy-development", output=tmp_path,
                                   subscription="subscription", resource_group="group"))
    assert len(patches) == 1
    assert json.loads((tmp_path / "deployment.json").read_text())["targets"][0]["image"] == image


def test_web_build_uses_frontend_dockerfile_and_reviewed_frontend_inputs(release, tmp_path, monkeypatch):
    import base64
    source = {"source_revision": "a" * 40, "source_inventory_sha256": "complete-source",
              "runtime_inventory_sha256": "frontend-inputs", "dependency_image": "node@sha256:pinned"}
    files = {"web/app/api/admin/health/route.ts": "reviewed", "web/package-lock.json": "locked"}
    monkeypatch.setattr(release, "verify", lambda role: source if role == "web" else pytest.fail("Wrong role"))
    monkeypatch.setattr(release, "acceptance", lambda: {"roles": {"web": {"runtime_files": files}}})
    calls = []
    monkeypatch.setattr(release, "azure", lambda *args: calls.append(args) or {"runId": "build"})
    release.build(SimpleNamespace(role="web", output=tmp_path, registry="registry", resource_group="group"))
    command = calls[0]
    assert command[command.index("--file") + 1].endswith("/Dockerfile.web")
    assert command[command.index("--target") + 1] == "runner"
    manifest = next(value.split("=", 1)[1] for value in command if value.startswith("WEB_SOURCE_FILES="))
    assert json.loads(base64.b64decode(manifest)) == files
    assert "DEPENDENCY_IMAGE=node@sha256:pinned" in command


def test_missing_role_prevents_a_partial_release(release, tmp_path, monkeypatch):
    images = tmp_path / "images.json"
    images.write_text(json.dumps({"images": {"worker": "registry@sha256:worker"}}))
    monkeypatch.setattr(release, "acceptance", lambda: {"roles": {
        "worker": {"development_targets": [{"name": "worker-dev"}]},
        "broker": {"development_targets": [{"name": "broker-dev"}]}}})
    monkeypatch.setattr(release, "azure", lambda *args: pytest.fail("Incomplete roles must fail before Azure"))
    with pytest.raises(ValueError, match="every reviewed deployment role"):
        release.deploy(SimpleNamespace(images=images, command="deploy-development"))


def test_unreviewed_live_consumer_prevents_all_image_changes(release, tmp_path, monkeypatch):
    image = "registry@sha256:worker"
    resource = {"name": "worker-dev", "identity": {}, "location": "westus", "properties": {
        "configuration": {}, "template": {"containers": [{"image": "previous"}]}}}
    target = {"kind": "jobs", "name": "worker-dev", "previous_image": "previous",
              "configuration_api_version": release.API_VERSION,
              "configuration_sha256": release.fingerprint(release.configuration(resource))}
    images = tmp_path / "images.json"
    images.write_text(json.dumps({"images": {"worker": image}, "source_inventory_sha256": "reviewed"}))
    monkeypatch.setattr(release, "acceptance", lambda: {"roles": {"worker": {"development_targets": [target]}}})
    monkeypatch.setattr(release, "verify", lambda role: {"source_inventory_sha256": "reviewed"})
    monkeypatch.setattr(release, "verify_image", lambda *args: None)
    def azure(*args):
        assert "patch" not in args
        return {"value": [] if "/containerApps?" in args[-1] else [resource, {**resource, "name": "forgotten-job"}]}
    monkeypatch.setattr(release, "azure", azure)
    with pytest.raises(ValueError, match="complete review"):
        release.deploy(SimpleNamespace(images=images, command="deploy-development", output=tmp_path,
                                       subscription="subscription", resource_group="group"))


def test_matching_hash_cannot_hide_a_recovery_job_pointed_at_development(release, monkeypatch):
    resource = {"name": "recovery", "identity": {}, "location": "westus", "properties": {
        "configuration": {"triggerType": "Schedule", "scheduleTriggerConfig": {
            "cronExpression": "7,22,37,52 * * * *"}}, "template": {"containers": [{"image": "accepted",
            "env": [{"name": "PIPELINE_JOB_NAME", "value": "system-recovery"},
                    {"name": "PIPELINE_DATASET_NAMESPACE", "value": "development"}]}]}}}
    target = {"name": "recovery", "kind": "jobs", "previous_image": "accepted",
              "configuration_api_version": release.API_VERSION,
              "configuration_sha256": release.fingerprint(release.configuration(resource)),
              "execution_binding": {"pipeline_job": "system-recovery", "dataset_namespace": "",
                                    "trigger": "Schedule", "cron": "7,22,37,52 * * * *"}}
    accepted = {"roles": {"worker": {"production_targets": [target]}}}
    monkeypatch.setattr(release, "azure", lambda *args: {"value": [] if "/containerApps?" in args[-1] else [resource]})
    with pytest.raises(ValueError, match="execution binding changed"):
        release.preflight_consumers(SimpleNamespace(command="deploy-production", subscription="subscription",
                                                   resource_group="group"), accepted, {"worker": "accepted"})
