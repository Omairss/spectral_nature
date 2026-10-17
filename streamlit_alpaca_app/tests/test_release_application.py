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
