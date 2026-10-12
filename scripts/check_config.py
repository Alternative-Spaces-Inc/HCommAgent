#!/usr/bin/env python3
"""Fail closed on malformed or unsafe candidate service configuration."""
import json
import sys

REQUIRED = {'DATABASE_URL', 'SWEEP_TRIGGER_TOKEN', 'HCA_ADMIN_TOKEN', 'HCA_API_KEY_PEPPER', 'HCA_WEBHOOK_ENCRYPTION_KEYS'}


def validate(service):
    spec = service.get('spec') or {}
    containers = ((spec.get('template') or {}).get('spec') or {}).get('containers') or spec.get('containers')
    if not isinstance(containers, list) or len(containers) != 1:
        raise ValueError('expected exactly one configured container')
    entries = containers[0].get('env')
    if not isinstance(entries, list):
        raise ValueError('container environment is missing')
    env = {item.get('name'): item for item in entries}
    def configured(name):
        entry = env.get(name) or {}
        ref = (entry.get('valueFrom') or {}).get('secretKeyRef') or {}
        return bool(entry.get('value') or (ref.get('name') and ref.get('key')))
    for name in REQUIRED:
        if not configured(name):
            raise ValueError('missing required credential reference: ' + name)
    for name, expected in (('HCA_ENV', 'production'), ('HCA_REQUIRE_GOOGLE_LINK', '1')):
        if (env.get(name) or {}).get('value') != expected:
            raise ValueError(name + ' must be ' + expected)
    if (env.get('HCA_WEBHOOK_ALLOW_INSECURE') or {}).get('value') not in (None, '', '0'):
        raise ValueError('insecure webhook switch is forbidden')
    if not (configured('HCA_GOOGLE_CLIENT_ID') or configured('HCA_GOOGLE_CLIENT_IDS')):
        raise ValueError('Google client IDs are missing')
    if not configured('HCA_WEBHOOK_CURRENT_KEY_ID'):
        raise ValueError('webhook current key ID is missing')


def main():
    try:
        service = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(sys.stdin)
        if not isinstance(service, dict):
            raise ValueError('expected a service object')
        validate(service)
    except (ValueError, TypeError, KeyError) as exc:
        print('FAIL check_config: ' + str(exc))
        return 1
    print('OK check_config: fail-closed production candidate configuration')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
