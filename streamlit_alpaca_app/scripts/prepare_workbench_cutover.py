#!/usr/bin/env python3
"""Prepare a frontend-only cutover on the existing production Container App.

No deployment, credential resolution, DNS change, or certificate transfer occurs.
The immutable output includes the exact original runtime configuration for rollback.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from prepare_workbench_release import API_VERSION, azure, fingerprint, pinned_image, writable_template


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resource-group', default='sn-pipeline-rg-03130136')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a new release directory; rollback records are immutable.')

    def app(name: str) -> dict:
        return azure('resource', 'show', '--resource-type', 'Microsoft.App/containerApps',
                     '--api-version', API_VERSION, '-g', args.resource_group, '-n', name)

    live, candidate, api = app('sn-streamlit-ui'), app('sn-next-ui'), app('sn-api-next')
    for resource in (live, candidate, api):
        pinned_image(resource)
    old, new = live['properties'], candidate['properties']
    if old['environmentId'].lower() != new['environmentId'].lower():
        raise ValueError('The candidate must use the existing production environment.')
    old_config = old['configuration']
    if old_config.get('activeRevisionsMode') != 'Single':
        raise ValueError('Review traffic routing before cutting over a multiple-revision app.')
    domains = old_config['ingress'].get('customDomains', [])
    if len(domains) != 4 or any(not d.get('certificateId') for d in domains):
        raise ValueError('All four production domains must retain their TLS bindings.')
    if api['properties']['configuration']['ingress']['external']:
        raise ValueError('The Python API must remain private.')

    template = writable_template(new['template'], job=False)
    template['containers'][0]['name'] = old['template']['containers'][0]['name']
    template['revisionSuffix'] = 'workbench-' + datetime.now(timezone.utc).strftime('%m%d%H%M%S')
    env = template['containers'][0].get('env', [])
    allowed = {'SPECTRAL_API_BASE_URL', 'APP_PUBLIC_ORIGINS', 'APP_COOKIE_SECURE', 'NODE_ENV', 'CODE_VERSION'}
    if {e['name'] for e in env} != allowed or any(e.get('secretRef') for e in env):
        raise ValueError('The web runtime must contain only the five accepted non-secret settings.')
    env_by_name = {e['name']: e for e in env}
    api_url = 'https://' + api['properties']['configuration']['ingress']['fqdn']
    if env_by_name['SPECTRAL_API_BASE_URL']['value'] != api_url:
        raise ValueError('The candidate is not wired to the accepted private production API.')
    if env_by_name['APP_COOKIE_SECURE']['value'] != 'true':
        raise ValueError('Secure cookies are required.')
    origins = env_by_name['APP_PUBLIC_ORIGINS']['value'].split(',')
    for host in [old_config['ingress']['fqdn'], *(d['name'] for d in domains)]:
        origin = 'https://' + host
        if origin not in origins:
            origins.append(origin)
    env_by_name['APP_PUBLIC_ORIGINS']['value'] = ','.join(origins)

    # Platform secret resolution and image pull still work. The browser-facing
    # Node process cannot request tokens from any of the retained identities.
    identities = list(live.get('identity', {}).get('userAssignedIdentities', {}))
    if live.get('identity', {}).get('type', '').startswith('SystemAssigned'):
        identities.append('system')
    identity_settings = [{'identity': identity, 'lifecycle': 'None'} for identity in identities]
    patch = {'properties': {'template': template, 'configuration': {
        'ingress': {'targetPort': new['configuration']['ingress']['targetPort']},
        'identitySettings': identity_settings,
    }}}
    rollback = {'properties': {
        'template': writable_template(old['template'], job=False),
        'configuration': {
            'ingress': {'targetPort': old_config['ingress']['targetPort']},
            'identitySettings': copy.deepcopy(old_config.get('identitySettings', [])),
        },
    }}

    def protected(resource: dict) -> dict:
        config = copy.deepcopy(resource['properties']['configuration'])
        config.pop('identitySettings', None)
        config['ingress'].pop('targetPort', None)
        return {'configuration': config, 'identity': resource.get('identity'),
                'environmentId': resource['properties']['environmentId']}

    record = {
        'prepared_at': datetime.now(timezone.utc).isoformat(),
        'status': 'prepared_not_applied', 'app_id': live['id'],
        'expected_properties_sha256': fingerprint(old),
        'protected_sha256': fingerprint(protected(live)),
        'original_revision': old['latestRevisionName'],
        'original_image': pinned_image(live),
        'web_image': pinned_image(candidate), 'private_api_image': pinned_image(api),
        'domains': domains, 'dns_change': False, 'credentials_changed': False,
        'broker_changes': False,
        'patch_sha256': hashlib.sha256(json.dumps(patch, sort_keys=True).encode()).hexdigest(),
    }
    args.output.mkdir(parents=True)
    for filename, content in [('cutover.patch.json', patch), ('rollback.patch.json', rollback), ('release.json', record)]:
        (args.output / filename).write_text(json.dumps(content, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'original_revision': record['original_revision'],
                      'web_image': record['web_image'], 'domains': [d['name'] for d in domains],
                      'dns_change': False, 'secrets_in_web_runtime': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
