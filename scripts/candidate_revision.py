#!/usr/bin/env python3
"""Bind validation, smoke and conditional traffic promotion to one immutable revision.

Only explicit CLI service, lease and promotion actions contact Cloud Run. Pure helpers and tests do
not require credentials. Snapshot files contain deployment identifiers, not
secret values. A changed service etag aborts promotion without a retry.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_config import DIGEST_IMAGE, check, capacity_plan  # noqa: E402

TAG = "candidate"  # Compatibility for inspecting historical deployments only.
API = "https://run.googleapis.com/v2/"
REVISION_TYPE = "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
RESOURCE = re.compile(r"^projects/[a-z0-9-]+/locations/[a-z0-9-]+/services/[a-z][a-z0-9-]{0,62}$")
BUILD = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
LEASE_OWNER = "hca-deployment-build"
LEASE_UNTIL = "hca-deployment-until"
LEASE_SECONDS = 2700  # Exceeds the pipeline's complete 1800-second build timeout.


class CandidateError(ValueError):
    pass


def _name(value):
    return str(value or "").rsplit("/", 1)[-1]


def _url(value):
    parsed = urlsplit(str(value or ""))
    if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".run.app")
            or parsed.port not in {None, 443} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise CandidateError("candidate URL must be a credential-free HTTPS Cloud Run URL")
    return str(value).rstrip("/")


def find_candidate(service: dict, tag: str = TAG) -> dict:
    if not isinstance(service, dict):
        raise CandidateError("service description must be an object")
    hits = [target for target in ((service.get("status") or {}).get("traffic") or []) if target.get("tag") == tag]
    if len(hits) != 1:
        raise CandidateError("expected exactly one build-scoped candidate tag")
    target = hits[0]
    if not target.get("revisionName") or not target.get("url"):
        raise CandidateError("candidate tag must have an explicit revision and URL")
    return target


def _validate_identity(commit, build, image, resource):
    if not COMMIT.fullmatch(str(commit)) or not BUILD.fullmatch(str(build)):
        raise CandidateError("a full commit SHA and Cloud Build UUID are required")
    if not DIGEST_IMAGE.fullmatch(str(image)):
        raise CandidateError("image must have an immutable 64-hex SHA256 digest")
    if not RESOURCE.fullmatch(str(resource)):
        raise CandidateError("invalid Cloud Run service resource")


def _v2_ready(service, revision, commit, build, image, resource):
    if not isinstance(service, dict) or service.get("name") != resource:
        raise CandidateError("Cloud Run service identity differs from this build")
    if service.get("reconciling") or not service.get("etag") or not service.get("uid"):
        raise CandidateError("service is reconciling or has no concurrency etag")
    if _name(service.get("latestCreatedRevision")) != revision or _name(service.get("latestReadyRevision")) != revision:
        raise CandidateError("another candidate exists or the candidate is not ready")
    if service.get("terminalCondition", {}).get("state") != "CONDITION_SUCCEEDED":
        raise CandidateError("Cloud Run reconciliation did not succeed")
    labels = service.get("template", {}).get("labels", {})
    if labels.get("commit") != commit or labels.get("build") != build:
        raise CandidateError("service template belongs to a different build")
    containers = service.get("template", {}).get("containers", [])
    if len(containers) != 1 or containers[0].get("image") != image:
        raise CandidateError("service template image differs from the validated digest")


def validate_retained_tags(service, candidate_revision=None, candidate_tag=None):
    statuses = service.get("trafficStatuses") or []
    serving = {_name(item.get("revision")) for item in statuses if item.get("percent", 0) > 0}
    for item in statuses + (service.get("traffic") or []):
        if not item.get("tag"):
            continue
        revision = _name(item.get("revision"))
        if item.get("tag") == candidate_tag and revision == candidate_revision:
            continue
        if revision not in serving:
            raise CandidateError("an additional tagged revision can retain instances; inspect and remove inactive tags before a new rollout")
    return sorted(serving)


def make_snapshot(service_v1, revision, service_v2, *, commit, build, image, resource, lease):
    _validate_identity(commit, build, image, resource)
    validate_lease(lease, service_v2)
    if lease["build"] != build or lease["resource"] != resource:
        raise CandidateError("deployment lease belongs to another build")
    tag = "candidate-" + build
    target = find_candidate(service_v1, tag)
    revision_name = target["revisionName"]
    if not re.fullmatch(re.escape(_name(resource)) + r"-[a-z0-9-]+", revision_name):
        raise CandidateError("candidate revision belongs to another service")
    if target.get("percent", 0) != 0:
        raise CandidateError("candidate already receives production traffic")
    problems = check(revision, commit, build, image, revision_name)
    if problems:
        raise CandidateError("; ".join(problems))
    _v2_ready(service_v2, revision_name, commit, build, image, resource)
    serving_revisions = validate_retained_tags(service_v2, revision_name, tag)
    env = {item["name"]: item.get("value", "") for item in revision["spec"]["containers"][0].get("env", [])}
    capacity = capacity_plan(env)
    url = _url(target["url"])
    statuses = [item for item in service_v2.get("trafficStatuses", []) if item.get("tag") == tag]
    if len(statuses) != 1 or _name(statuses[0].get("revision")) != revision_name or _url(statuses[0].get("uri")) != url:
        raise CandidateError("v1/v2 candidate revision and URL disagree")
    traffic = service_v2.get("traffic") or []
    matching = [item for item in traffic if item.get("tag") == tag and _name(item.get("revision")) == revision_name]
    if len(matching) != 1 or matching[0].get("percent", 0) != 0:
        raise CandidateError("desired candidate traffic differs from observed traffic")
    # Preserve other tags and resolve every target explicitly: no LATEST
    # assignment can float when a subsequent build creates another revision.
    promotion = []
    for item in traffic:
        if item.get("type") != REVISION_TYPE or not item.get("revision"):
            raise CandidateError("traffic must be pinned to explicit revisions before validation")
        entry = {"type": REVISION_TYPE, "revision": _name(item["revision"]),
                 "percent": 100 if item.get("tag") == tag else 0}
        if item.get("tag"):
            entry["tag"] = item["tag"]
        promotion.append(entry)
    return {"version": 1, "resource": resource, "revision": revision_name, "url": url,
            "image": image, "commit": commit, "build": build, "tag": tag,
            "etag": service_v2["etag"], "uid": service_v2.get("uid"), "lease": deepcopy(lease),
            "previous_traffic": deepcopy(service_v2.get("trafficStatuses") or []),
            "serving_revisions": serving_revisions, "declared_capacity": capacity,
            "promotion_traffic": promotion, "config_validated": True, "smoke_passed": False}


def load_snapshot(path):
    with open(path) as source:
        snapshot = json.load(source)
    _validate_identity(snapshot["commit"], snapshot["build"], snapshot["image"], snapshot["resource"])
    if snapshot.get("version") != 1 or snapshot.get("tag") != "candidate-" + snapshot["build"] or not snapshot.get("etag"):
        raise CandidateError("invalid candidate snapshot")
    _url(snapshot["url"])
    return snapshot


def save_snapshot(path, snapshot):
    destination = Path(path)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(snapshot, sort_keys=True, indent=2) + "\n")
    temporary.replace(destination)


def _existing_service(service, resource):
    if (not RESOURCE.fullmatch(str(resource)) or not isinstance(service, dict)
            or service.get("name") != resource or not service.get("uid") or not service.get("etag")
            or service.get("reconciling") or service.get("terminalCondition", {}).get("state") != "CONDITION_SUCCEEDED"):
        raise CandidateError("an existing ready service with a UID and etag is required; bootstrap separately")


def validate_lease(lease, service, *, now=None):
    """A service may change etag during our deploy; ownership and UID must persist."""
    if not isinstance(lease, dict) or lease.get("version") != 1 or not BUILD.fullmatch(str(lease.get("build"))):
        raise CandidateError("invalid deployment lease")
    _existing_service(service, lease.get("resource"))
    labels = service.get("labels") or {}
    expires = str(lease.get("expires_at", ""))
    if (not re.fullmatch(r"[1-9][0-9]{0,11}", expires) or service["uid"] != lease.get("uid")
            or labels.get(LEASE_OWNER) != lease["build"] or labels.get(LEASE_UNTIL) != expires):
        raise CandidateError("deployment lease owner or service identity changed")
    if int(expires) <= (time.time() if now is None else now):
        raise CandidateError("deployment lease expired; inspect the terminal build and recover manually")


def load_lease(path):
    with open(path) as source:
        return json.load(source)


def _wait_operation(operation, request_json, action, *, timeout=120, sleep=time.sleep):
    deadline = time.monotonic() + timeout
    while not operation.get("done"):
        if not operation.get("name") or time.monotonic() >= deadline:
            raise CandidateError(action + " did not finish within its deadline; inspect service state before retrying")
        sleep(1)
        operation = request_json("GET", operation["name"])
    if operation.get("error"):
        raise CandidateError(action + " operation failed; inspect service state before retrying")


def acquire_lease(resource, build, request_json, *, now=None):
    """CAS acquisition never steals an existing lease, including an expired one."""
    if not BUILD.fullmatch(str(build)):
        raise CandidateError("a Cloud Build UUID is required for deployment ownership")
    current = request_json("GET", resource)
    _existing_service(current, resource)
    serving = validate_retained_tags(current)
    if not serving:
        raise CandidateError("existing production traffic is required; bootstrap separately")
    labels = deepcopy(current.get("labels") or {})
    if LEASE_OWNER in labels or LEASE_UNTIL in labels:
        if labels.get(LEASE_OWNER) != build:
            raise CandidateError("deployment lease already exists; wait for success or explicit operator recovery")
        lease = {"version": 1, "resource": resource, "build": build, "uid": current["uid"],
                 "expires_at": labels.get(LEASE_UNTIL)}
        validate_lease(lease, current, now=now)
        return lease
    expires = int(time.time() if now is None else now) + LEASE_SECONDS
    lease = {"version": 1, "resource": resource, "build": build, "uid": current["uid"], "expires_at": str(expires)}
    labels.update({LEASE_OWNER: build, LEASE_UNTIL: str(expires)})
    body = {"name": resource, "etag": current["etag"], "labels": labels}
    _wait_operation(request_json("PATCH", resource + "?updateMask=labels", body), request_json, "lease acquisition")
    final = request_json("GET", resource)
    validate_lease(lease, final, now=now)
    return lease


def check_lease(lease, request_json):
    validate_lease(lease, request_json("GET", lease["resource"]))


def release_lease(lease, request_json):
    current = request_json("GET", lease["resource"])
    validate_lease(lease, current)
    labels = deepcopy(current.get("labels") or {})
    labels.pop(LEASE_OWNER)
    labels.pop(LEASE_UNTIL)
    body = {"name": lease["resource"], "etag": current["etag"], "labels": labels}
    _wait_operation(request_json("PATCH", lease["resource"] + "?updateMask=labels", body), request_json, "lease release")
    final = request_json("GET", lease["resource"])
    _existing_service(final, lease["resource"])
    if final["uid"] != lease["uid"] or any(key in (final.get("labels") or {}) for key in (LEASE_OWNER, LEASE_UNTIL)):
        raise CandidateError("lease release could not be verified; inspect service state")
    return {"build": lease["build"], "released": True, "etag": final["etag"]}


def promotion_body(snapshot, current):
    if not snapshot.get("config_validated") or not snapshot.get("smoke_passed"):
        raise CandidateError("candidate configuration and smoke must pass before promotion")
    validate_lease(snapshot["lease"], current)
    if snapshot["lease"]["build"] != snapshot["build"] or snapshot["lease"]["resource"] != snapshot["resource"]:
        raise CandidateError("candidate snapshot differs from the owned deployment lease")
    _v2_ready(current, snapshot["revision"], snapshot["commit"], snapshot["build"], snapshot["image"], snapshot["resource"])
    if current.get("etag") != snapshot["etag"] or current.get("uid") != snapshot["uid"]:
        raise CandidateError("service changed after validation; abandon this candidate and revalidate a new build")
    statuses = [item for item in current.get("trafficStatuses", []) if item.get("tag") == snapshot["tag"]]
    if len(statuses) != 1 or _name(statuses[0].get("revision")) != snapshot["revision"] or _url(statuses[0].get("uri")) != snapshot["url"]:
        raise CandidateError("candidate tag changed after validation")
    traffic = snapshot["promotion_traffic"]
    active = [item for item in traffic if item.get("percent", 0) > 0]
    if (sum(item.get("percent", 0) for item in active) != 100
            or any(item.get("type") != REVISION_TYPE or _name(item.get("revision")) != snapshot["revision"] for item in active)):
        raise CandidateError("promotion plan differs from the validated revision")
    return {"name": snapshot["resource"], "etag": snapshot["etag"], "traffic": deepcopy(traffic)}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CandidateError("Cloud Run API redirect refused")


class CloudRunAPI:
    def __init__(self):
        result = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, timeout=30)
        if result.returncode or not result.stdout.strip():
            raise CandidateError("could not obtain deployment credentials")
        self.token = result.stdout.strip()
        self.opener = urllib.request.build_opener(_NoRedirect())

    def __call__(self, method, resource, payload=None):
        if not re.fullmatch(r"projects/[a-z0-9-]+/locations/[a-z0-9-]+/(services/[a-z0-9-]+|operations/[A-Za-z0-9_-]+)(\?updateMask=(traffic|labels))?", resource):
            raise CandidateError("invalid Cloud Run API resource")
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(API + resource, data=body, method=method,
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in {409, 412}:
                raise CandidateError("concurrent service update refused; inspect lease and traffic before recovery") from None
            raise CandidateError(f"Cloud Run API returned HTTP {exc.code}; response content omitted") from None
        except urllib.error.URLError:
            raise CandidateError("Cloud Run API unavailable") from None


def promote(snapshot, request_json, *, timeout=120, sleep=time.sleep):
    current = request_json("GET", snapshot["resource"])
    body = promotion_body(snapshot, current)
    operation = request_json("PATCH", snapshot["resource"] + "?updateMask=traffic", body)
    _wait_operation(operation, request_json, "promotion", timeout=timeout, sleep=sleep)
    final = request_json("GET", snapshot["resource"])
    validate_lease(snapshot["lease"], final)
    if final.get("reconciling"):
        raise CandidateError("service still reconciling after promotion")
    active = [item for item in final.get("trafficStatuses", []) if item.get("percent", 0) > 0]
    if sum(item.get("percent", 0) for item in active) != 100 or any(_name(item.get("revision")) != snapshot["revision"] for item in active):
        raise CandidateError("traffic verification differs from the exact validated revision")
    return {"revision": snapshot["revision"], "image": snapshot["image"], "commit": snapshot["commit"],
            "build": snapshot["build"], "promoted": True, "etag": final.get("etag")}


def main():
    # Retain historical inspection CLI, not used by the build pipeline.
    if any(flag in sys.argv[1:] for flag in ("--name", "--url")):
        parser = argparse.ArgumentParser(description=__doc__)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--name", action="store_true")
        group.add_argument("--url", action="store_true")
        parser.add_argument("--tag", default=TAG)
        args = parser.parse_args()
        try:
            target = find_candidate(json.load(sys.stdin), args.tag)
            print(target["revisionName"] if args.name else target["url"].rstrip("/"))
            return 0
        except (CandidateError, ValueError, TypeError, AttributeError):
            print("FAIL candidate_revision: invalid candidate service description", file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    service = commands.add_parser("service")
    service.add_argument("--resource", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--resource", required=True)
    lease_acquire = commands.add_parser("lease-acquire")
    for name in ("resource", "build", "lease"):
        lease_acquire.add_argument("--" + name, required=True)
    for name in ("lease-check", "lease-release"):
        command = commands.add_parser(name)
        command.add_argument("--lease", required=True)
    image = commands.add_parser("image")
    for name in ("path", "repository", "commit", "build", "resource"):
        image.add_argument("--" + name, required=True)
    capture = commands.add_parser("capture")
    for name in ("service-v1", "revision-json", "service-v2", "commit", "build", "image", "resource", "snapshot", "lease"):
        capture.add_argument("--" + name, required=True)
    field = commands.add_parser("field")
    field.add_argument("--snapshot", required=True)
    field.add_argument("--field", choices=["revision", "url", "image", "commit", "build", "tag"], required=True)
    for name in ("smoked", "promote"):
        command = commands.add_parser(name)
        command.add_argument("--snapshot", required=True)
    args = parser.parse_args()
    try:
        if args.command == "lease-acquire":
            lease = acquire_lease(args.resource, args.build, CloudRunAPI())
            save_snapshot(args.lease, lease)
            print("Acquired deployment lease for build " + args.build)
        elif args.command in {"lease-check", "lease-release"}:
            lease = load_lease(args.lease)
            if args.command == "lease-check":
                check_lease(lease, CloudRunAPI())
                print("Verified deployment ownership")
            else:
                print(json.dumps(release_lease(lease, CloudRunAPI())))
        elif args.command == "image":
            image = Path(args.path).read_text().strip()
            _validate_identity(args.commit, args.build, image, args.resource)
            if image.rsplit("@", 1)[0] != args.repository:
                raise CandidateError("pushed image repository differs from this build")
            print("Validated immutable build image")
        elif args.command in {"service", "preflight"}:
            if not RESOURCE.fullmatch(args.resource):
                raise CandidateError("invalid service resource")
            service = CloudRunAPI()("GET", args.resource)
            if args.command == "preflight":
                validate_retained_tags(service)
                print("OK capacity preflight: retained tags reference only serving revisions")
            else:
                print(json.dumps(service))
        elif args.command == "capture":
            def read(path):
                with open(path) as source:
                    return json.load(source)
            snapshot = make_snapshot(read(args.service_v1), read(args.revision_json), read(args.service_v2),
                commit=args.commit, build=args.build, image=args.image, resource=args.resource, lease=load_lease(args.lease))
            save_snapshot(args.snapshot, snapshot)
            print("Validated candidate " + snapshot["revision"])
        elif args.command == "field":
            print(load_snapshot(args.snapshot)[args.field])
        elif args.command == "smoked":
            snapshot = load_snapshot(args.snapshot)
            if not snapshot.get("config_validated"):
                raise CandidateError("candidate was not configured safely")
            snapshot["smoke_passed"] = True
            save_snapshot(args.snapshot, snapshot)
        else:
            print(json.dumps(promote(load_snapshot(args.snapshot), CloudRunAPI())))
        return 0
    except CandidateError as exc:
        print("FAIL candidate_revision: " + str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        print("FAIL candidate_revision: invalid candidate data; inspect build and service state", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
