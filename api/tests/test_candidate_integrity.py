"""Simulate overlapping Cloud Run builds and bind every stage to the checked revision."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import candidate_revision as candidate  # noqa: E402
import check_config  # noqa: E402

COMMIT = 'a' * 40
BUILD = '01234567-89ab-cdef-0123-456789abcdef'
RESOURCE = 'projects/demo-project/locations/us-west1/services/hca-api-service'
REVISION = 'hca-api-service-00042-abc'
IMAGE = 'us-west1-docker.pkg.dev/demo-project/hca-services/hca-api@sha256:' + 'b' * 64
TAG = 'candidate-' + BUILD
URL = 'https://' + TAG + '---hca.example.run.app'


def descriptions():
    env = [{'name': name, 'valueFrom': {'secretKeyRef': {'name': name.lower(), 'key': 'latest'}}}
           for name in sorted(check_config.REQUIRED_SECRETS)]
    env += [{'name': name, 'value': value} for name, value in check_config.CAPACITY_DEFAULTS.items()]
    env += [{'name': 'HCA_GOOGLE_CLIENT_ID', 'value': 'client.apps.googleusercontent.com'},
            {'name': 'HCA_OWNER_EMAILS', 'value': 'owner@example.com'},
            {'name': 'HCA_REQUIRE_GOOGLE_LINK', 'value': '1'}, {'name': 'HCA_ENV', 'value': 'production'}]
    revision = {'metadata': {'name': REVISION, 'labels': {'commit': COMMIT, 'build': BUILD},
                'annotations': {'autoscaling.knative.dev/maxScale': '2'}},
                'spec': {'containers': [{'image': IMAGE, 'env': env}]}, 'status': {'imageDigest': IMAGE}}
    traffic = [{'type': candidate.REVISION_TYPE, 'revision': 'hca-api-service-00041-old', 'percent': 100},
               {'type': candidate.REVISION_TYPE, 'revision': REVISION, 'percent': 0, 'tag': TAG}]
    v2 = {'name': RESOURCE, 'uid': 'service-uid', 'etag': 'checked-etag', 'reconciling': False,
          'latestReadyRevision': RESOURCE + '/revisions/' + REVISION,
          'latestCreatedRevision': RESOURCE + '/revisions/' + REVISION,
          'terminalCondition': {'state': 'CONDITION_SUCCEEDED'},
          'labels': {candidate.LEASE_OWNER: BUILD, candidate.LEASE_UNTIL: '4102444800'},
          'template': {'labels': {'commit': COMMIT, 'build': BUILD}, 'containers': [{'image': IMAGE}]},
          'traffic': traffic,
          'trafficStatuses': [{**t, 'uri': URL if t.get('tag') else 'https://hca.example.run.app'} for t in traffic]}
    v1 = {'status': {'traffic': [{'revisionName': REVISION, 'tag': TAG, 'url': URL, 'percent': 0}]}}
    return v1, revision, v2


def lease_record():
    return {'version': 1, 'resource': RESOURCE, 'build': BUILD, 'uid': 'service-uid', 'expires_at': '4102444800'}


def snapshot():
    v1, revision, v2 = descriptions()
    return candidate.make_snapshot(v1, revision, v2, commit=COMMIT, build=BUILD, image=IMAGE, resource=RESOURCE, lease=lease_record())


@pytest.mark.parametrize('changed', ['commit', 'build', 'image', 'revision', 'url', 'ready', 'production_traffic'])
def test_capture_refuses_wrong_candidate_or_partial_reconciliation(changed):
    v1, revision, v2 = descriptions()
    if changed in {'commit', 'build'}:
        revision['metadata']['labels'][changed] = 'other-build'
    elif changed == 'image':
        revision['spec']['containers'][0]['image'] = IMAGE.replace('b' * 64, 'c' * 64)
    elif changed == 'revision':
        revision['metadata']['name'] += '-different'
    elif changed == 'url':
        v2['trafficStatuses'][1]['uri'] = 'https://wrong.example.run.app'
    elif changed == 'ready':
        v2['reconciling'] = True
    else:
        v1['status']['traffic'][0]['percent'] = 100
    with pytest.raises(candidate.CandidateError):
        candidate.make_snapshot(v1, revision, v2, commit=COMMIT, build=BUILD, image=IMAGE, resource=RESOURCE, lease=lease_record())


def test_saved_snapshot_preserves_exact_identity_between_steps(tmp_path):
    expected = snapshot()
    path = tmp_path / 'hca-candidate.json'
    candidate.save_snapshot(path, expected)
    assert candidate.load_snapshot(path) == expected
    result = subprocess.run([sys.executable, str(SCRIPTS / 'candidate_revision.py'), 'field',
                             '--snapshot', str(path), '--field', 'url'], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == URL
    assert not expected['smoke_passed']


def test_promotion_uses_immutable_revision_and_etag_precondition():
    snap = snapshot()
    snap['smoke_passed'] = True
    _, _, v2 = descriptions()
    requests = []
    final = deepcopy(v2)
    final['etag'] = 'promoted-etag'
    final['trafficStatuses'] = [{'revision': REVISION, 'percent': 100}]

    def api(method, resource, payload=None):
        requests.append((method, resource, payload))
        if method == 'PATCH':
            assert payload['etag'] == 'checked-etag'
            active = [target for target in payload['traffic'] if target['percent']]
            assert active == [{'type': candidate.REVISION_TYPE, 'revision': REVISION, 'percent': 100, 'tag': TAG}]
            return {'done': True, 'response': final}
        return v2 if len(requests) == 1 else final

    result = candidate.promote(snap, api)
    assert result['promoted'] and result['revision'] == REVISION and result['image'] == IMAGE
    assert [method for method, _, _ in requests] == ['GET', 'PATCH', 'GET']


@pytest.mark.parametrize('overlap', ['etag', 'new_candidate', 'uid', 'image', 'tag'])
def test_overlapping_deploy_or_service_change_aborts_without_patch(overlap):
    snap = snapshot()
    snap['smoke_passed'] = True
    _, _, current = descriptions()
    if overlap == 'etag':
        current['etag'] = 'other-build-etag'
    elif overlap == 'new_candidate':
        current['latestCreatedRevision'] = RESOURCE + '/revisions/hca-api-service-00043-other'
    elif overlap == 'uid':
        current['uid'] = 'recreated-service'
    elif overlap == 'image':
        current['template']['containers'][0]['image'] = IMAGE.replace('b' * 64, 'd' * 64)
    else:
        current['trafficStatuses'][1]['revision'] = 'hca-api-service-00043-other'
    requests = []

    def api(method, resource, payload=None):
        requests.append(method)
        return current

    with pytest.raises(candidate.CandidateError):
        candidate.promote(snap, api)
    assert requests == ['GET']


def test_change_between_get_and_patch_is_not_retried_or_promoted():
    snap = snapshot()
    snap['smoke_passed'] = True
    _, _, current = descriptions()
    calls = []

    def api(method, resource, payload=None):
        calls.append(method)
        if method == 'GET':
            return current
        # Cloud Run rejects this submitted etag if another writer changed it
        # after GET. No unguarded update or automatic retry follows.
        assert payload['etag'] == 'checked-etag'
        raise candidate.CandidateError('concurrent service update refused')

    with pytest.raises(candidate.CandidateError, match='concurrent'):
        candidate.promote(snap, api)
    assert calls == ['GET', 'PATCH']


@pytest.mark.parametrize('failure', ['operation_error', 'missing_operation', 'wrong_final_traffic'])
def test_failed_or_unverified_promotion_never_reports_success(failure):
    snap = snapshot()
    snap['smoke_passed'] = True
    _, _, current = descriptions()
    calls = []

    def api(method, resource, payload=None):
        calls.append(method)
        if method == 'PATCH':
            if failure == 'operation_error':
                return {'done': True, 'error': {'code': 9}}
            if failure == 'missing_operation':
                return {'done': False}
            return {'done': True}
        # The unmodified service still serves its previous revision after an
        # unsuccessful operation, so final verification must refuse it.
        return current

    with pytest.raises(candidate.CandidateError):
        candidate.promote(snap, api)
    assert calls[:2] == ['GET', 'PATCH']


def test_unsmoked_candidate_and_corrupt_promotion_plan_are_refused():
    snap = snapshot()
    _, _, current = descriptions()
    with pytest.raises(candidate.CandidateError, match='smoke'):
        candidate.promotion_body(snap, current)
    snap['smoke_passed'] = True
    snap['promotion_traffic'][1]['revision'] = 'hca-api-service-00043-other'
    with pytest.raises(candidate.CandidateError, match='plan'):
        candidate.promotion_body(snap, current)


def test_config_cannot_borrow_sidecar_secrets_and_rejects_development():
    _, revision, _ = descriptions()
    revision['spec']['containers'].append(deepcopy(revision['spec']['containers'][0]))
    assert any('exactly one' in problem for problem in check_config.check(revision))
    revision['spec']['containers'] = revision['spec']['containers'][:1]
    env = revision['spec']['containers'][0]['env']
    next(value for value in env if value['name'] == 'HCA_ENV')['value'] = 'development'
    assert any('HCA_ENV' in problem for problem in check_config.check(revision))


def test_image_validation_precedes_production_mutations_and_pipeline_shells_parse(tmp_path):
    pipeline = yaml.safe_load((ROOT / 'cloudbuild.yaml').read_text())
    ids = [step['id'] for step in pipeline['steps']]
    assert ids.index('image-identity') < ids.index('migrate') < ids.index('deploy') < ids.index('check-config') < ids.index('smoke-candidate') < ids.index('promote')
    steps = {step['id']: step for step in pipeline['steps']}
    assert steps['build']['args'] == ['build', '-t',
        '${_REGION}-docker.pkg.dev/${PROJECT_ID}/${_AR_REPO}/${_IMAGE}:${COMMIT_SHA}', '.']
    assert '--to-latest' not in json.dumps(pipeline)
    assert 'candidate-${BUILD_ID}' in steps['deploy']['args'][-1]
    assert '${_MIGRATE_SA}' in steps['migrate']['args'][-1]
    assert '${_MIGRATE_DB_URL_SECRET}' in steps['migrate']['args'][-1]
    for name in ('check-config', 'smoke-candidate', 'promote'):
        assert '/workspace/hca-candidate.json' in steps[name]['args'][-1]
        assert '/tmp/hca-candidate.json' not in steps[name]['args'][-1]
    for step in pipeline['steps']:
        if step.get('entrypoint') == 'bash' or step['id'] == 'guard':
            script = tmp_path / (step['id'] + '.sh')
            script.write_text(step['args'][-1].replace('$$', '$'))
            checked = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True)
            assert checked.returncode == 0, step['id'] + ': ' + checked.stderr


@pytest.mark.parametrize('image', [IMAGE.replace('b' * 64, 'b' * 63), IMAGE.replace('@sha256:' + 'b' * 64, ':latest')])
def test_digest_validation_is_fail_closed_before_migration(image):
    with pytest.raises(candidate.CandidateError, match='digest'):
        candidate._validate_identity(COMMIT, BUILD, image, RESOURCE)


def _migration_module():
    # Execute the definitions without starting Alembic's environment. This
    # exercises the real lock/recovery functions using a fake connection.
    source = (ROOT / 'api/alembic/env.py').read_text()
    source = source[:source.index('\nif context.is_offline_mode():')]
    from unittest.mock import patch
    from alembic import context
    from alembic.config import Config
    with patch.object(context, 'config', Config(), create=True):
        namespace = {'__file__': str(ROOT / 'api/alembic/env.py')}
        exec(compile(source, 'migration-environment', 'exec'), namespace)
    return namespace


def test_migration_lock_timeout_is_bounded_and_never_terminates_by_default(monkeypatch):
    module = _migration_module()
    values = iter([0, 2, 2])
    monkeypatch.setattr(module['time'], 'monotonic', lambda: next(values))
    module['SERIALIZE_SECONDS'] = 1
    queries = []

    class Connection:
        def execute(self, query, args):
            queries.append((str(query), args))
            return type('Result', (), {'scalar': lambda self: False})()
        def commit(self):
            pass

    with pytest.raises(RuntimeError, match='another migration'):
        module['_acquire_migration_lock'](Connection())
    assert len(queries) == 1 and 'pg_try_advisory_lock' in queries[0][0]
    monkeypatch.delenv('HCA_MIGRATE_TERMINATE_IDLE', raising=False)
    module['_terminate_idle_in_transaction'](Connection())
    assert len(queries) == 1


def test_opt_in_migration_recovery_requires_runtime_role_and_excludes_unnamed_clients(monkeypatch):
    module = _migration_module()
    monkeypatch.setenv('HCA_MIGRATE_TERMINATE_IDLE', '1')
    monkeypatch.delenv('HCA_MIGRATE_RECOVERY_ROLE', raising=False)
    with pytest.raises(SystemExit):
        module['_terminate_idle_in_transaction'](object())
    monkeypatch.setenv('HCA_MIGRATE_RECOVERY_ROLE', 'hca_runtime')
    queries = []

    class Connection:
        def execute(self, query, args):
            queries.append((str(query), args))
            return type('Result', (), {'fetchall': lambda self: []})()

    module['_terminate_idle_in_transaction'](Connection())
    query, args = queries[0]
    assert "application_name = 'hca-api'" in query and 'usename = :runtime_role' in query
    assert 'current_database()' in query and 'coalesce' not in query and args['runtime_role'] == 'hca_runtime'


def test_declared_connection_budget_fits_overlap_and_rejects_previous_unsafe_defaults():
    plan = check_config.capacity_plan(check_config.CAPACITY_DEFAULTS)
    assert plan == {'new_revision': 8, 'old_revision_reserve': 15, 'migration_reserve': 2, 'total': 25, 'budget': 25}
    previous = {**check_config.CAPACITY_DEFAULTS, 'HCA_MAX_INSTANCES': '3', 'HCA_DB_MAX_OVERFLOW': '2'}
    with pytest.raises(ValueError, match='exceeds'):
        check_config.capacity_plan(previous)
    # Preserve an owner-selected instance maximum when an explicit larger
    # application budget actually covers the configured overlap.
    assert check_config.capacity_plan({**previous, 'HCA_DB_CONNECTION_BUDGET': '32'})['total'] == 32


@pytest.mark.parametrize('value', ['0', '-1', '1.5', '10000', ' 2', '2s', ''])
def test_connection_capacity_fields_are_positive_bounded_integers(value):
    for name in check_config.CAPACITY_DEFAULTS:
        with pytest.raises(ValueError, match=name):
            check_config.capacity_plan({**check_config.CAPACITY_DEFAULTS, name: value})


def test_capacity_guard_runs_before_mutations_and_rejects_bad_values():
    pipeline = yaml.safe_load((ROOT / 'cloudbuild.yaml').read_text())
    steps = {step['id']: step for step in pipeline['steps']}
    assert '--capacity-from-env' in steps['guard']['args'][-1]
    ids = list(steps)
    assert ids.index('capacity-preflight') < ids.index('migrate') < ids.index('deploy')
    assert steps['guard']['env'].count('HCA_DB_CONNECTION_BUDGET=${_DB_CONNECTION_BUDGET}') == 1
    _, revision, _ = descriptions()
    revision['metadata']['annotations']['autoscaling.knative.dev/maxScale'] = '3'
    assert any('maxScale' in problem for problem in check_config.check(revision, COMMIT, BUILD, IMAGE, REVISION))


def test_retained_inactive_revision_tags_are_refused_but_serving_aliases_are_allowed():
    v1, revision, v2 = descriptions()
    v2['trafficStatuses'].append({'revision': 'hca-api-service-00040-retained', 'percent': 0, 'tag': 'old-candidate',
                                 'uri': 'https://old-candidate---hca.example.run.app'})
    with pytest.raises(candidate.CandidateError, match='additional tagged'):
        candidate.make_snapshot(v1, revision, v2, commit=COMMIT, build=BUILD, image=IMAGE, resource=RESOURCE, lease=lease_record())
    with pytest.raises(candidate.CandidateError, match='additional tagged'):
        candidate.validate_retained_tags(v2)
    v2['trafficStatuses'][-1]['revision'] = 'hca-api-service-00041-old'
    allowed = candidate.make_snapshot(v1, revision, v2, commit=COMMIT, build=BUILD, image=IMAGE, resource=RESOURCE, lease=lease_record())
    assert allowed['serving_revisions'] == ['hca-api-service-00041-old']
    assert allowed['declared_capacity']['total'] == 25


class LeaseService:
    """Emulate atomic etag writes so simultaneous acquisitions share real state."""
    def __init__(self):
        self.service = descriptions()[2]
        self.service['labels'] = {'environment': 'production'}
        self.service['traffic'] = self.service['traffic'][:1]
        self.service['trafficStatuses'] = self.service['trafficStatuses'][:1]
        self.calls = []

    def __call__(self, method, resource, payload=None):
        self.calls.append((method, resource, deepcopy(payload)))
        if method == 'GET':
            return deepcopy(self.service)
        assert resource == RESOURCE + '?updateMask=labels'
        if payload['etag'] != self.service['etag']:
            raise candidate.CandidateError('concurrent service update refused')
        self.service['labels'] = deepcopy(payload['labels'])
        self.service['etag'] += '-changed'
        return {'done': True}


def test_lease_serializes_candidate_warming_and_preserves_labels(monkeypatch):
    api = LeaseService()
    monkeypatch.setattr(candidate.time, 'time', lambda: 1000)
    lease = candidate.acquire_lease(RESOURCE, BUILD, api)
    assert lease['expires_at'] == str(1000 + candidate.LEASE_SECONDS)
    assert api.service['labels']['environment'] == 'production'
    assert api.calls[1][2]['etag'] == 'checked-etag'
    assert set(api.calls[1][2]) == {'name', 'etag', 'labels'}
    other_build = 'abcdef01-2345-6789-abcd-ef0123456789'
    with pytest.raises(candidate.CandidateError, match='already exists'):
        candidate.acquire_lease(RESOURCE, other_build, api)
    # Retrying the same build preserves its original deadline instead of
    # extending a lease that could outlive the complete build timeout.
    assert candidate.acquire_lease(RESOURCE, BUILD, api) == lease
    assert [method for method, _, _ in api.calls].count('PATCH') == 1
    monkeypatch.setattr(candidate.time, 'time', lambda: 4000)
    with pytest.raises(candidate.CandidateError, match='already exists'):
        candidate.acquire_lease(RESOURCE, other_build, api)
    with pytest.raises(candidate.CandidateError, match='expired'):
        candidate.acquire_lease(RESOURCE, BUILD, api)
    assert [method for method, _, _ in api.calls].count('PATCH') == 1


def test_two_simultaneous_lease_reads_only_one_can_commit(monkeypatch):
    api = LeaseService()
    stale = deepcopy(api.service)
    monkeypatch.setattr(candidate.time, 'time', lambda: 1000)
    candidate.acquire_lease(RESOURCE, BUILD, api)
    calls = []

    def stale_read(method, resource, payload=None):
        calls.append(method)
        return deepcopy(stale) if method == 'GET' else api(method, resource, payload)

    with pytest.raises(candidate.CandidateError, match='concurrent'):
        candidate.acquire_lease(RESOURCE, 'abcdef01-2345-6789-abcd-ef0123456789', stale_read)
    assert calls == ['GET', 'PATCH']
    assert api.service['labels'][candidate.LEASE_OWNER] == BUILD


def test_lease_refuses_missing_service_and_retained_warm_candidates():
    api = LeaseService()
    api.service['trafficStatuses'].append({'revision': REVISION, 'tag': TAG, 'percent': 0})
    with pytest.raises(candidate.CandidateError, match='additional tagged'):
        candidate.acquire_lease(RESOURCE, BUILD, api)
    assert all(method != 'PATCH' for method, _, _ in api.calls)
    with pytest.raises(candidate.CandidateError, match='existing ready service'):
        candidate.acquire_lease(RESOURCE, BUILD, lambda *args: {})


@pytest.mark.parametrize('change', ['owner', 'uid', 'expired', 'missing'])
def test_lease_loss_blocks_capture_and_promotion_without_mutation(change):
    v1, revision, current = descriptions()
    snap = snapshot()
    snap['smoke_passed'] = True
    if change == 'owner':
        current['labels'][candidate.LEASE_OWNER] = 'abcdef01-2345-6789-abcd-ef0123456789'
    elif change == 'uid':
        current['uid'] = 'another-service'
    elif change == 'expired':
        snap['lease']['expires_at'] = '1'
        current['labels'][candidate.LEASE_UNTIL] = '1'
    else:
        current['labels'].pop(candidate.LEASE_OWNER)
    lease = snap['lease']
    with pytest.raises(candidate.CandidateError, match='lease'):
        candidate.make_snapshot(v1, revision, current, commit=COMMIT, build=BUILD, image=IMAGE, resource=RESOURCE, lease=lease)
    calls = []
    def api(method, resource, payload=None):
        calls.append(method)
        return current
    with pytest.raises(candidate.CandidateError, match='lease'):
        candidate.promote(snap, api)
    assert calls == ['GET']


def test_only_owner_can_release_and_release_uses_current_etag(monkeypatch):
    api = LeaseService()
    monkeypatch.setattr(candidate.time, 'time', lambda: 1000)
    lease = candidate.acquire_lease(RESOURCE, BUILD, api)
    # Our own deploy and promotion change the service etag while preserving
    # ownership. Release uses the final etag, rather than acquisition's.
    api.service['etag'] = 'promoted-etag'
    result = candidate.release_lease(lease, api)
    assert result['released'] and api.calls[-2][2]['etag'] == 'promoted-etag'
    assert api.service['labels'] == {'environment': 'production'}
    api = LeaseService()
    lease = candidate.acquire_lease(RESOURCE, BUILD, api)
    api.service['labels'][candidate.LEASE_OWNER] = 'abcdef01-2345-6789-abcd-ef0123456789'
    with pytest.raises(candidate.CandidateError, match='owner'):
        candidate.release_lease(lease, api)
    assert [method for method, _, _ in api.calls].count('PATCH') == 1


def test_pipeline_owns_lease_before_privileged_actions_and_releases_after_final_smoke():
    pipeline = yaml.safe_load((ROOT / 'cloudbuild.yaml').read_text())
    steps = {step['id']: step for step in pipeline['steps']}
    ids = list(steps)
    assert ids.index('image-identity') < ids.index('deployment-lease') < ids.index('migrate')
    assert ids.index('smoke-live') < ids.index('release-deployment-lease')
    assert candidate.LEASE_SECONDS > int(pipeline['timeout'].rstrip('s'))
    for name in ('migrate', 'deploy', 'check-config', 'smoke-candidate', 'smoke-live'):
        assert 'lease-check --lease /workspace/hca-deployment-lease.json' in steps[name]['args'][-1]
    assert '--lease /workspace/hca-deployment-lease.json' in steps['check-config']['args'][-1]


@pytest.mark.parametrize('name,value,canonical', [
    ('HCA_MIGRATE_LOCK_TIMEOUT', '1ms', '1ms'),
    ('HCA_MIGRATE_LOCK_TIMEOUT', '300000ms', '300000ms'),
    ('HCA_MIGRATE_LOCK_TIMEOUT', '5min', '300000ms'),
    ('HCA_MIGRATE_IDLE_TXN_GRACE', '1440 minutes', '86400000ms'),
    ('HCA_MIGRATE_IDLE_TXN_GRACE', '86400 seconds', '86400000ms'),
    ('HCA_MIGRATE_SERIALIZE_SECONDS', '300', 300),
])
def test_migration_duration_boundaries_are_parsed_by_units(monkeypatch, name, value, canonical):
    monkeypatch.setenv(name, value)
    module = _migration_module()
    variable = {'HCA_MIGRATE_LOCK_TIMEOUT': 'LOCK_TIMEOUT', 'HCA_MIGRATE_IDLE_TXN_GRACE': 'IDLE_TXN_GRACE',
                'HCA_MIGRATE_SERIALIZE_SECONDS': 'SERIALIZE_SECONDS'}[name]
    assert module[variable] == canonical


@pytest.mark.parametrize('name,value', [
    ('HCA_MIGRATE_LOCK_TIMEOUT', '0s'), ('HCA_MIGRATE_LOCK_TIMEOUT', '300001ms'),
    ('HCA_MIGRATE_LOCK_TIMEOUT', '6min'), ('HCA_MIGRATE_LOCK_TIMEOUT', '999999min'),
    ('HCA_MIGRATE_LOCK_TIMEOUT', '1s; SELECT 1'), ('HCA_MIGRATE_LOCK_TIMEOUT', 'infinity'),
    ('HCA_MIGRATE_IDLE_TXN_GRACE', '0 seconds'), ('HCA_MIGRATE_IDLE_TXN_GRACE', '86401 seconds'),
    ('HCA_MIGRATE_IDLE_TXN_GRACE', '1441 minutes'), ('HCA_MIGRATE_IDLE_TXN_GRACE', 'NaN seconds'),
    ('HCA_MIGRATE_SERIALIZE_SECONDS', '0'), ('HCA_MIGRATE_SERIALIZE_SECONDS', '301'),
    ('HCA_MIGRATE_SERIALIZE_SECONDS', '9999'),
])
def test_migration_duration_zero_overflow_and_nonliteral_values_refuse_before_connect(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit, match='refusing'):
        _migration_module()


def test_runtime_upgrade_preserves_optional_crypto_settings_and_removes_insecure_switch():
    pipeline = yaml.safe_load((ROOT / 'cloudbuild.yaml').read_text())
    deploy = next(step for step in pipeline['steps'] if step['id'] == 'deploy')['args'][-1]
    assert '--update-env-vars=' in deploy and '--update-secrets=' in deploy
    assert '--set-env-vars=' not in deploy and '--set-secrets=' not in deploy
    assert '--clear-env-vars' not in deploy and '--clear-secrets' not in deploy
    assert '--remove-env-vars=HCA_WEBHOOK_ALLOW_INSECURE' in deploy
    for name in check_config.OPTIONAL_SECRET_ENV | {check_config.CURRENT_KEY_ID}:
        assert name not in deploy  # No default enabling or automatic retirement.
    _, revision, _ = descriptions()
    env = revision['spec']['containers'][0]['env']
    for name in check_config.OPTIONAL_SECRET_ENV:
        env.append({'name': name, 'valueFrom': {'secretKeyRef': {'name': 'optional-transition', 'key': '7'}}})
    env.append({'name': check_config.CURRENT_KEY_ID, 'value': 'webhook.v2'})
    env.append({'name': 'HCA_MAX_ACTIVE_DEVICES', 'value': '100'})
    assert check_config.check(revision, COMMIT, BUILD, IMAGE, REVISION) == []
    # A preserved development switch still prevents candidate promotion.
    env.append({'name': 'HCA_LOCAL_AUTH_BYPASS', 'value': '1'})
    assert any('development switch' in error for error in check_config.check(revision))


@pytest.mark.parametrize('setting', sorted(check_config.OPTIONAL_SECRET_ENV))
def test_optional_transition_material_must_never_be_plaintext(setting):
    _, revision, _ = descriptions()
    env = revision['spec']['containers'][0]['env']
    env.append({'name': setting, 'value': 'do-not-print-this-value'})
    if setting == 'HCA_WEBHOOK_ENCRYPTION_KEYS':
        env.append({'name': check_config.CURRENT_KEY_ID, 'value': 'webhook.v2'})
    errors = check_config.check(revision)
    assert any(setting in error and 'Secret Manager' in error for error in errors)
    assert 'do-not-print-this-value' not in ';'.join(errors)


@pytest.mark.parametrize('present', ['ring', 'id'])
def test_orphaned_encryption_keyring_or_current_id_refuses_candidate(present):
    _, revision, _ = descriptions()
    env = revision['spec']['containers'][0]['env']
    if present == 'ring':
        env.append({'name': 'HCA_WEBHOOK_ENCRYPTION_KEYS', 'valueFrom': {'secretKeyRef': {'name': 'ring', 'key': '7'}}})
    else:
        env.append({'name': check_config.CURRENT_KEY_ID, 'value': 'webhook.v2'})
    assert any('configured together' in error for error in check_config.check(revision))



def test_alembic_preserves_percent_encoded_credentials_without_interpolation_error(monkeypatch):
    encoded = 'postgresql+psycopg://operator:synthetic%40password%25only@localhost/test'
    monkeypatch.setenv('DATABASE_URL', encoded)
    module = _migration_module()
    assert module['config'].get_main_option('sqlalchemy.url') == encoded



@pytest.mark.parametrize('key_id', ['', 'k' * 41, 'bad/key'])
def test_current_key_id_bounds_match_runtime_policy(key_id):
    _, revision, _ = descriptions()
    env = revision['spec']['containers'][0]['env']
    env.append({'name': 'HCA_WEBHOOK_ENCRYPTION_KEYS', 'valueFrom': {'secretKeyRef': {'name': 'ring', 'key': '7'}}})
    env.append({'name': check_config.CURRENT_KEY_ID, 'value': key_id})
    assert any('valid identifier' in error for error in check_config.check(revision))



@pytest.mark.parametrize('approved,expected', [('', 1), ('b' * 40, 1), ('not-a-full-commit', 1), (COMMIT, 0)])
def test_cloud_guard_requires_explicit_exact_commit_approval(approved, expected):
    import os
    pipeline = yaml.safe_load((ROOT / 'cloudbuild.yaml').read_text())
    assert pipeline['substitutions']['_APPROVED_COMMIT'] == ''
    guard = next(step for step in pipeline['steps'] if step['id'] == 'guard')
    environment = {**os.environ, **check_config.CAPACITY_DEFAULTS,
                   'APPROVED_COMMIT': approved, 'CURRENT_COMMIT': COMMIT,
                   'CLIENT_ID': 'client.apps.googleusercontent.com', 'CLIENT_IDS': '',
                   'REQ': '1', 'ALLOW_UNGATED': '0', 'MIGRATE_SA': 'migrator',
                   'RUNTIME_SA': 'runtime', 'MIGRATE_SECRET': 'ddl-secret', 'RUNTIME_SECRET': 'data-secret'}
    result = subprocess.run(['bash', '-c', guard['args'][-1].replace('$$', '$')], cwd=ROOT,
                            env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == expected
    if expected:
        assert 'exact tested commit approval' in result.stdout
        assert 'OK capacity' not in result.stdout  # Refuses before subsequent stages.
    else:
        assert 'OK capacity' in result.stdout and 'gate pinned' in result.stdout
