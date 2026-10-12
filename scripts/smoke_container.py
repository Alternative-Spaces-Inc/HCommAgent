#!/usr/bin/env python3
"""Exercise a local runtime export; OCI scan/attestation remains separate evidence.

Uses only temporary Docker resources and loopback HTTP. Generated credentials,
container logs and API bodies are captured rather than printed or retained.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FS_PROBE = """
import json, os
from pathlib import Path
assert os.getuid() == 10001 and os.getgid() == 10001, 'unexpected runtime identity'
for name in ('/app/.hca-smoke-write', '/app/app/.hca-smoke-write'):
    path = Path(name)
    try:
        path.write_text('synthetic')
    except PermissionError:
        pass
    else:
        path.unlink()
        raise RuntimeError('application tree is writable')
data = Path('/data/.hca-smoke-write')
data.write_text('synthetic')
assert data.read_text() == 'synthetic'
data.unlink()
print(json.dumps({'uid': os.getuid(), 'gid': os.getgid(), 'app_readonly': True, 'data_writable': True}))
"""
PID_PROBE = """
import json, os
from pathlib import Path
parts = Path('/proc/1/cmdline').read_bytes().split(b'\\0')
assert parts[0].rsplit(b'/', 1)[-1].startswith(b'python')
assert any(b'uvicorn' in part for part in parts)
status = Path('/proc/1/status').read_text().splitlines()
uid = next(line for line in status if line.startswith('Uid:')).split()[1:]
assert all(value == '10001' for value in uid) and os.getuid() == 10001
print(json.dumps({'pid1_exec': True, 'pid1_uid': 10001}))
"""


class SmokeError(RuntimeError):
    pass


def run(command, *, env=None, timeout=60, check=True):
    try:
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise SmokeError('local container command unavailable or exceeded its deadline; output omitted') from None
    if check and result.returncode:
        raise SmokeError('local container command failed; output omitted')
    return result.stdout.strip()


def require_local_endpoint(endpoint):
    if isinstance(endpoint, str) and (endpoint.startswith('unix:///') or endpoint.startswith('npipe:////./pipe/')):
        return
    parsed = urlsplit(str(endpoint))
    if parsed.scheme == 'tcp' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'} and not parsed.username:
        return
    raise SmokeError('runtime smoke requires a local Docker endpoint')


def loopback_url(published):
    match = re.fullmatch(r'127\.0\.0\.1:([0-9]{1,5})', published)
    if not match or not 1 <= int(match[1]) <= 65535:
        raise SmokeError('runtime smoke must publish exactly one loopback port')
    return 'http://' + published


def wait_health(base, timeout=30):
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        try:
            with opener.open(base + '/healthz', timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    raise SmokeError('runtime did not become healthy within its deadline')


def smoke(image):
    if not re.fullmatch(r'[a-z0-9][a-z0-9._/:@-]+', image):
        raise SmokeError('invalid local runtime image name')
    endpoint = os.environ.get('DOCKER_HOST')
    if os.environ.get('DOCKER_CONTEXT') or not endpoint:
        endpoint = json.loads(run(['docker', 'context', 'inspect', '--format', '{{json .Endpoints.docker.Host}}']))
    require_local_endpoint(endpoint)
    # Resolve once and execute the exact loaded image ID throughout the smoke.
    identity = json.loads(run(['docker', 'image', 'inspect', '--format', '{{json .Id}}', image]))
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', identity):
        raise SmokeError('runtime export has no immutable local image ID')
    filesystem = json.loads(run(['docker', 'run', '--rm', '--pull=never', '--network=none', identity,
                                 'python', '-c', FS_PROBE]))
    resource = 'hca-smoke-' + secrets.token_hex(12)
    migration = resource + '-migration'
    environment = {name: value for name, value in os.environ.items()
                   if name.lower() not in {'http_proxy', 'https_proxy', 'all_proxy'}}
    environment.update({'HCA_ADMIN_TOKEN': secrets.token_urlsafe(32),
                        'HCA_API_KEY_PEPPER': secrets.token_urlsafe(32),
                        'NO_PROXY': '127.0.0.1,localhost,::1', 'no_proxy': '127.0.0.1,localhost,::1'})
    mount = 'type=volume,source=' + resource + ',target=/data'
    try:
        run(['docker', 'volume', 'create', '--label', 'hca.container-smoke=true', resource])
        run(['docker', 'run', '--rm', '--name', migration, '--pull=never', '--network=none',
             '--mount', mount, '--env', 'DATABASE_URL=sqlite:////data/smoke.db',
             identity, 'alembic', 'upgrade', 'head'], timeout=90)
        # No command/entrypoint/init override: verify the image's real CMD and
        # signal forwarding rather than a test-specific launch wrapper.
        run(['docker', 'run', '--detach', '--name', resource, '--label', 'hca.container-smoke=true',
             '--pull=never', '--mount', mount, '--publish', '127.0.0.1::8080',
             '--env', 'DATABASE_URL=sqlite:////data/smoke.db', '--env', 'HCA_ENV=development',
             '--env', 'HCA_REQUIRE_GOOGLE_LINK=0', '--env', 'HCA_ADMIN_TOKEN',
             '--env', 'HCA_API_KEY_PEPPER', identity], env=environment)
        base = loopback_url(run(['docker', 'port', resource, '8080/tcp']))
        wait_health(base)
        process = json.loads(run(['docker', 'exec', resource, 'python', '-c', PID_PROBE]))
        run([sys.executable, str(ROOT / 'scripts/smoke_golden_path.py'), '--base', base],
            env=environment, timeout=120)
        started = time.monotonic()
        run(['docker', 'kill', '--signal=SIGTERM', resource])
        exit_code = int(run(['docker', 'wait', resource], timeout=20))
        duration = time.monotonic() - started
        state = json.loads(run(['docker', 'inspect', '--format', '{{json .State}}', resource]))
        logs = subprocess.run(['docker', 'logs', resource], capture_output=True, text=True, timeout=20)
        shutdown = 'Application shutdown complete.' in logs.stdout + logs.stderr
        # Uvicorn restores and re-raises SIGTERM after its graceful shutdown,
        # yielding 143 on Linux. A forced kill or incomplete shutdown fails.
        if exit_code not in {0, 143} or state.get('OOMKilled') or not shutdown or duration >= 20:
            raise SmokeError('runtime did not complete a prompt graceful SIGTERM shutdown')
        return {'runtime_export': image, 'runtime_image_id': identity, **filesystem, **process,
                'health': True, 'agent_http_smoke': True, 'shutdown_complete': True,
                'signal': 'SIGTERM', 'exit_code': exit_code, 'shutdown_seconds': round(duration, 3),
                'oci_attestation_scope': 'separate OCI export of the same Dockerfile'}
    finally:
        # Cleanup names were generated by this invocation; no broad pruning.
        commands = [['docker', 'rm', '--force', name] for name in (resource, migration)]
        commands.append(['docker', 'volume', 'rm', resource])
        for command in commands:
            try:
                run(command, check=False)
            except SmokeError:
                pass  # Continue trying every scoped resource if the daemon stops.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='hca-review:runtime')
    parser.add_argument('--output', default='hca-container-runtime.json')
    args = parser.parse_args()
    try:
        evidence = smoke(args.image)
        Path(args.output).write_text(json.dumps(evidence, sort_keys=True, indent=2) + '\n')
        print('OK: local runtime UID/filesystem, HTTP and graceful shutdown verified')
        return 0
    except (SmokeError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print('FAIL: local runtime smoke; private command output omitted', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
