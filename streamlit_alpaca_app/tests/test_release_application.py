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
        if "patch" in args:
            patches.append(True)
        else:
            reads.append(True)
            if len(reads) >= 4:
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
