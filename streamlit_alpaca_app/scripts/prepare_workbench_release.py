#!/usr/bin/env python3
"""Prepare a production workbench release from live config; never mutate Azure.

Generated app templates contain secret references, not resolved credentials.
Existing producer updates use template-only PATCH to preserve write-only secrets.
Production app deployment, pipeline promotion, and domain cutover are separate steps.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

API_VERSION = "2025-07-01"
ARM_SCHEMA = "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#"
API_ENV_KEYS = {
    "AZURE_CLIENT_ID", "AZURE_KEY_VAULT_NAME", "KEY_VAULT_NAME",
    "AZURE_STORAGE_ACCOUNT_URL", "AZURE_STORAGE_CONTAINER", "POSTGRES_CONNECTION_STRING_SECRET_NAME",
    "CONTROL_POSTGRES_CONNECTION_STRING", "APP_ACCESS_SCHEMA", "DASHBOARD_AUTH_ENABLED",
    "DASHBOARD_AUTH_MODE", "APP_PUBLIC_BASE_URL", "DASHBOARD_AUTH_BOOTSTRAP_ADMIN_EMAIL_SECRET",
    "DASHBOARD_AUTH_BOOTSTRAP_ADMIN_PASSWORD_SECRET", "APCA_API_KEY_SECRET_NAME",
    "APCA_API_SECRET_KEY_SECRET_NAME", "APCA_API_BASE_URL", "ALPACA_DATA_BASE_URL",
    "FRED_API_KEY_SECRET_NAME", "LLM_PROVIDER", "LLM_MODEL", "LLM_DEPLOYMENT",
    "LLM_API_KEY_SECRET_NAME", "LLM_BASE_URL", "LLM_TEMPERATURE", "LLM_TIMEOUT_SECONDS",
    "APP_SMTP_HOST", "APP_SMTP_PORT", "APP_SMTP_USE_TLS", "APP_SMTP_USE_SSL",
    "APP_SMTP_USERNAME_SECRET", "APP_SMTP_PASSWORD_SECRET", "APP_EMAIL_FROM_SECRET",
}
BROKER_ENV_KEYS = {
    "ROBINHOOD_MCP_TOKEN_ENCRYPTION_KEY", "ROBINHOOD_MAIN_EXECUTION_ENABLED",
    "ROBINHOOD_ORDER_CONFIRMATION_TTL_SECONDS",
}


def azure(*args: str) -> dict:
    result = subprocess.run(["az", *args, "-o", "json"], capture_output=True, text=True, timeout=90)
    if result.returncode:
        # CLI errors can include a request body; never echo them into release logs.
        raise RuntimeError(f"Azure read/validation failed: {' '.join(args[:3])}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def pinned_image(resource: dict) -> str:
    image = resource["properties"]["template"]["containers"][0]["image"]
    if not re.fullmatch(r"[a-z0-9.-]+\.azurecr\.io/[a-z0-9/_-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError(f"Image must be pinned by digest: {resource['name']}")
    props = resource["properties"]
    if "latestRevisionName" in props and props["latestRevisionName"] != props.get("latestReadyRevisionName"):
        raise ValueError(f"Latest revision is not ready: {resource['name']}")
    return image


def resolve_existing_image(resource: dict) -> str:
    image = resource["properties"]["template"]["containers"][0]["image"]
    if "@sha256:" in image:
        return image
    registry, repository = image.split("/", 1)
    details = azure("acr", "repository", "show", "--name", registry.split(".")[0], "--image", repository)
    digest = details["digest"]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("Could not pin the existing rollback image.")
    return f"{registry}/{repository.split(':', 1)[0]}@{digest}"


def environment(resource: dict) -> dict[str, dict]:
    return {item["name"]: copy.deepcopy(item) for item in resource["properties"]["template"]["containers"][0].get("env", [])}


def secret_reference(resource: dict, definition: dict) -> dict:
    name = definition["name"]
    if definition.get("keyVaultUrl"):
        return {key: definition[key] for key in ("name", "keyVaultUrl", "identity")}
    if not re.fullmatch(r"[a-z0-9-]+", name) or not re.fullmatch(r"[a-z0-9-]+", resource["name"]):
        raise ValueError("Unexpected native secret identifier.")
    # Native secret values are write-only on resource GET. Resolve them inside
    # Azure at deployment time; never fetch them on the operator workstation.
    value = (f"[filter(listSecrets(resourceId('{resource['type']}', '{resource['name']}'), '{API_VERSION}').value, "
             f"lambda('secret', equals(lambdaVariables('secret').name, '{name}')))[0].value]")
    return {"name": name, "value": value}


def arm(resources: list[dict]) -> dict:
    return {"$schema": ARM_SCHEMA, "contentVersion": "1.0.0.0", "resources": resources}


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def writable_template(source: dict, *, job: bool) -> dict:
    keys = {"containers", "initContainers", "volumes"} | (set() if job else {"scale"})
    template = {key: copy.deepcopy(value) for key, value in source.items() if key in keys and value is not None}
    container_keys = {"name", "image", "env", "resources", "probes", "volumeMounts", "command", "args"}
    for key in ("containers", "initContainers"):
        if key in template:
            template[key] = [{name: value for name, value in container.items() if name in container_keys and value is not None}
                             for container in template[key]]
    return template


def resource_config(source: dict, name: str, *, kind: str) -> dict:
    props = source["properties"]
    configuration = {key: copy.deepcopy(value) for key, value in props["configuration"].items() if value is not None}
    configuration.pop("dapr", None)
    configuration.pop("service", None)
    configuration.pop("deploymentLabels", None)
    configuration.pop("identitySettings", None)
    configuration["registries"] = [
        {key: value for key, value in registry.items() if key in {"server", "identity"} and value}
        for registry in configuration.get("registries", [])
    ]
    configuration["secrets"] = []
    template = writable_template(props["template"], job=kind == "jobs")
    template["containers"] = [template["containers"][0]]
    template["containers"][0].pop("env", None)
    template["containers"][0].pop("command", None)
    template["containers"][0].pop("args", None)
    result = {
        "type": f"Microsoft.App/{kind}", "apiVersion": API_VERSION,
        "name": name, "location": source["location"],
        "identity": {"type": "UserAssigned", "userAssignedIdentities": {
            key: {} for key in source["identity"]["userAssignedIdentities"]
        }},
        "properties": {"environmentId": props["environmentId"], "configuration": configuration, "template": template},
    }
    if props.get("workloadProfileName"):
        result["properties"]["workloadProfileName"] = props["workloadProfileName"]
    return result


def probes(port: int) -> list[dict]:
    return [{"type": kind, "httpGet": {"path": "/health", "port": port, "scheme": "HTTP"},
             "initialDelaySeconds": 5, "periodSeconds": 10, "timeoutSeconds": 3,
             "failureThreshold": 10 if kind == "Startup" else 3}
            for kind in ("Startup", "Readiness", "Liveness")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", default="sn-pipeline-rg-03130136")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    group = args.resource_group
    prepared_at = datetime.now(timezone.utc)
    args.output = args.output or Path("infra/.generated/workbench-production") / prepared_at.strftime("%Y%m%dT%H%M%SZ")
    if args.output.exists():
        raise ValueError("Release output already exists. Prepare a new release rather than overwriting rollback state.")

    def app(name: str) -> dict:
        return azure("resource", "show", "--resource-type", "Microsoft.App/containerApps",
                     "--api-version", API_VERSION, "-g", group, "-n", name)

    def job(name: str) -> dict:
        return azure("resource", "show", "--resource-type", "Microsoft.App/jobs",
                     "--api-version", API_VERSION, "-g", group, "-n", name)

    old_ui, old_api = app("sn-streamlit-ui"), app("sn-api")
    dev_ui, dev_api = app("sn-next-ui-dev"), app("sn-api-next-dev")
    worker, dev_home, old_home = job("sn-research-run-dev"), job("attention-home-next-dev"), job("attention-home-build")
    web_image, api_image = pinned_image(dev_ui), pinned_image(dev_api)
    worker_image, pipeline_image = pinned_image(worker), pinned_image(dev_home)
    prod_env, broker_env = environment(old_api), environment(old_ui)
    if prod_env.get("DASHBOARD_AUTH_ENABLED", {}).get("value", "").lower() != "true":
        raise ValueError("Production authentication must be enabled.")
    old_domains = old_ui["properties"]["configuration"]["ingress"].get("customDomains", [])
    if not old_domains or any(not domain.get("certificateId") for domain in old_domains):
        raise ValueError("Every production hostname must retain an existing TLS certificate.")

    api = resource_config(dev_api, "sn-api-next", kind="containerApps")
    api_container = api["properties"]["template"]["containers"][0]
    api_container["image"] = api_image
    api_environment = {key: item for key, item in prod_env.items() if key in API_ENV_KEYS}
    api_environment.update({key: item for key, item in broker_env.items() if key in BROKER_ENV_KEYS})
    api_environment.update({key: {"name": key, "value": value} for key, value in {
        "APP_TRACK": "production", "PIPELINE_DATASET_NAMESPACE": "production",
        "RESEARCH_RUN_JOB_NAME": "sn-research-run", "PIPELINE_RESOURCE_GROUP": group,
        "API_ACCESS_TOKEN_SECRET_NAME": environment(dev_api)["API_ACCESS_TOKEN_SECRET_NAME"]["value"],
        "APP_DISABLE_CACHE": "false", "CODE_VERSION": environment(dev_api)["CODE_VERSION"]["value"],
    }.items()})
    for key in ("CONTROL_POSTGRES_CONNECTION_STRING", "ROBINHOOD_MCP_TOKEN_ENCRYPTION_KEY"):
        if not api_environment.get(key, {}).get("secretRef"):
            raise ValueError(f"{key} must be a native secret reference, never a literal value.")
    api_container["env"] = list(api_environment.values())
    api_container["probes"] = probes(8080)
    api["properties"]["template"]["scale"] = {"minReplicas": 1, "maxReplicas": 1}
    api["properties"]["configuration"]["ingress"].update({"external": False, "targetPort": 8080, "customDomains": [], "allowInsecure": False})

    required_secrets = {item["secretRef"] for item in api_environment.values() if item.get("secretRef")}
    definitions = {item["name"]: (source, item) for source in (old_api, old_ui) for item in source["properties"]["configuration"].get("secrets", [])}
    for name in sorted(required_secrets):
        source_definition = definitions.get(name)
        if not source_definition:
            raise ValueError(f"Production secret reference is undefined: {name}")
        secret_source, definition = source_definition
        api["properties"]["configuration"]["secrets"].append(secret_reference(secret_source, definition))

    executor = resource_config(worker, "sn-research-run", kind="jobs")
    executor["properties"]["configuration"]["triggerType"] = "Manual"
    executor["properties"]["configuration"].pop("scheduleTriggerConfig", None)
    executor["properties"]["configuration"].pop("eventTriggerConfig", None)
    executor_container = executor["properties"]["template"]["containers"][0]
    executor_container["image"] = worker_image
    worker_env = environment(worker)
    for key in ("APP_TRACK", "PIPELINE_DATASET_NAMESPACE"):
        worker_env[key] = {"name": key, "value": "production"}
    if any(item.get("secretRef") for item in worker_env.values()):
        raise ValueError("Worker native secret references require an explicit review.")
    executor_container["env"] = list(worker_env.values())

    web = resource_config(dev_ui, "sn-next-ui", kind="containerApps")
    web_container = web["properties"]["template"]["containers"][0]
    web_container["image"] = web_image
    suffix = dev_ui["properties"]["configuration"]["ingress"]["fqdn"].split(".", 1)[1]
    web_origin = f"https://sn-next-ui.{suffix}"
    origins = [web_origin, *(f"https://{domain['name']}" for domain in old_domains)]
    web_container["env"] = [{"name": key, "value": value} for key, value in {
        "SPECTRAL_API_BASE_URL": f"https://sn-api-next.internal.{suffix}",
        "APP_PUBLIC_ORIGINS": ",".join(origins), "APP_COOKIE_SECURE": "true",
        "NODE_ENV": "production", "CODE_VERSION": environment(dev_ui)["CODE_VERSION"]["value"],
    }.items()]
    web_container["probes"] = probes(3000)
    web["properties"]["template"]["scale"] = {"minReplicas": 1, "maxReplicas": 1}
    web["properties"]["configuration"]["ingress"].update({"external": True, "targetPort": 3000, "customDomains": [], "allowInsecure": False})
    # Registry identity remains available to the platform, not application code.
    web["properties"]["configuration"]["identitySettings"] = [
        {"identity": identity, "lifecycle": "None"} for identity in web["identity"]["userAssignedIdentities"]
    ]

    # The API dispatcher already uses this existing identity; grant only the new
    # one-run worker scope, not subscription/resource-group Jobs Operator access.
    dispatcher_identity = next(identity for identity in api["identity"]["userAssignedIdentities"] if identity.lower().endswith("/developer-1"))
    job_operator = {
        "type": "Microsoft.Authorization/roleAssignments", "apiVersion": "2022-04-01",
        "name": "[guid(resourceId('Microsoft.App/jobs', 'sn-research-run'), 'workbench-dispatcher')]",
        "scope": "[resourceId('Microsoft.App/jobs', 'sn-research-run')]",
        "dependsOn": ["[resourceId('Microsoft.App/jobs', 'sn-research-run')]"],
        "properties": {
            "roleDefinitionId": "[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b9a307c4-5aa3-4b52-ba60-2b17c136cd7b')]",
            "principalId": worker["identity"]["userAssignedIdentities"][dispatcher_identity]["principalId"],
            "principalType": "ServicePrincipal",
        },
    }
    producer_updates, producer_rollbacks = [], []
    for producer in (old_home, job("equities-intraday-preload")):
        # PATCH only the versioned template. Never PUT the existing job: native
        # secrets are write-only, and listSecrets(self) creates an ARM cycle.
        original_template = writable_template(producer["properties"]["template"], job=True)
        rollback_template = copy.deepcopy(original_template)
        rollback_template["containers"][0]["image"] = resolve_existing_image(producer)
        pipeline_template = copy.deepcopy(original_template)
        pipeline_container = pipeline_template["containers"][0]
        pipeline_container["image"] = pipeline_image
        pipeline_env = environment(producer)
        for key, value in {"CODE_VERSION": environment(dev_home)["CODE_VERSION"]["value"],
                           "APP_TRACK": "production", "PIPELINE_DATASET_NAMESPACE": "production"}.items():
            pipeline_env[key] = {"name": key, "value": value}
        pipeline_container["env"] = list(pipeline_env.values())
        url = f"https://management.azure.com{producer['id']}?api-version={API_VERSION}"
        protected = {key: producer.get(key) for key in ("identity", "location")}
        protected["configuration"] = producer["properties"]["configuration"]
        protected["environmentId"] = producer["properties"]["environmentId"]
        common = {"method": "PATCH", "url": url, "job": producer["name"],
                  "expected_configuration_sha256": fingerprint(protected)}
        producer_updates.append({**common, "expected_template_sha256": fingerprint(original_template),
                                 "body": {"properties": {"template": pipeline_template}}})
        producer_rollbacks.append({**common, "expected_template_sha256": fingerprint(pipeline_template),
                                   "body": {"properties": {"template": rollback_template}}})

    args.output.mkdir(parents=True, exist_ok=False)
    files = {
        "candidate.apps.json": arm([executor, job_operator, api, web]),
        "existing-producers.patch.json": {"status": "prepared_not_applied", "operations": producer_updates},
        "rollback-producers.patch.json": {"status": "prepared_not_applied", "operations": producer_rollbacks},
        "release.json": {
            "status": "prepared_not_deployed", "prepared_at": prepared_at.isoformat(), "resource_group": group,
            "candidate_origin": web_origin, "images": {"web": web_image, "api": api_image, "worker": worker_image, "pipeline": pipeline_image},
            "existing_production_ui": {"id": old_ui["id"], "revision": old_ui["properties"]["latestReadyRevisionName"],
                                       "image": old_ui["properties"]["template"]["containers"][0]["image"], "domains": old_domains},
            "production_home_schedule": old_home["properties"]["configuration"].get("scheduleTriggerConfig"),
            "producer_promotion": "Apply template-only PATCH operations after comparing current configuration/template fingerprints. Do not submit these bundles as ARM templates.",
            "cutover": "Explicit approval required. Preserve existing DNS and certificate bindings until candidate acceptance.",
        },
    }
    for name, contents in files.items():
        (args.output / name).write_text(json.dumps(contents, indent=2) + "\n")
    print(json.dumps({"status": "prepared_not_deployed", "files": [str(args.output / name) for name in files], "candidate_origin": web_origin}, indent=2))


if __name__ == "__main__":
    main()
