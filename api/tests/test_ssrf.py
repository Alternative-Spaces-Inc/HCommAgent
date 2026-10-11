"""HCA-P21a: the destination URL rules (app/ssrf.py), each proven without
DNS through an injected resolver."""
import pytest

from app import ssrf

PUBLIC = "93.184.216.34"
PUBLIC6 = "2606:2800:220:1:248:1893:25c8:1946"


def _resolver(table):
    return lambda host: table.get(host, [])


def test_https_public_host_passes_and_pins_addresses():
    checked = ssrf.check_url("https://hooks.example/path?x=1", resolver=_resolver({"hooks.example": [PUBLIC, PUBLIC6]}), insecure=False)
    assert checked.host == "hooks.example"
    assert checked.port == 443
    assert checked.addresses == sorted([PUBLIC, PUBLIC6])


@pytest.mark.parametrize("url,reason", [
    ("http://hooks.example/h", "scheme must be https"),
    ("ftp://hooks.example/h", "scheme must be https"),
    ("https://user:pw@hooks.example/h", "userinfo"),
    ("https:///h", "no host"),
    ("https://hooks.example:22/h", "port must be 443 or above 1023"),
    ("https://hooks.example:80/h", "port must be 443 or above 1023"),
    ("https://hooks.example:abc/h", "port is not a number"),
    ("https://metadata.google.internal/computeMetadata/v1/", "metadata"),
    ("https://169.254.169.254/latest/meta-data/", "metadata"),
    ("https://anything.internal/h", "metadata"),
    ("https://localhost/h", "loopback host"),
    ("https://api.localhost/h", "loopback host"),
    ("https://127.0.0.1/h", "loopback address"),
    ("https://[::1]/h", "loopback address"),
    ("https://10.1.2.3/h", "private address"),
    ("https://172.16.5.5/h", "private address"),
    ("https://192.168.0.9/h", "private address"),
    ("https://100.64.1.1/h", "address"),
    ("https://0.0.0.0/h", "unspecified"),
    ("https://169.254.10.10/h", "link local"),
    ("https://224.0.0.1/h", "multicast"),
    ("https://[fe80::1]/h", "link local"),
    ("https://[fc00::1]/h", "private"),
    ("https://[::ffff:10.0.0.1]/h", "private address (inside"),
    ("https://[::ffff:127.0.0.1]/h", "loopback address (inside"),
    ("https://[2002:0a00:0001::1]/h", "inside"),
    ("https://[2001:0:0a00:0001::1]/h", "inside"),
])
def test_forbidden_urls_are_rejected(url, reason):
    with pytest.raises(ssrf.DestinationRejected) as exc:
        ssrf.check_url(url, resolver=_resolver({}), insecure=False)
    assert reason in str(exc.value)


@pytest.mark.parametrize("addresses,reason", [
    (["10.0.0.5"], "private"),
    ([PUBLIC, "127.0.0.1"], "loopback"),
    (["::ffff:169.254.169.254"], "link local address (inside"),
    (["fe80::1"], "link local"),
    ([], "does not resolve"),
])
def test_resolved_addresses_are_judged(addresses, reason):
    with pytest.raises(ssrf.DestinationRejected) as exc:
        ssrf.check_url("https://hooks.example/h", resolver=_resolver({"hooks.example": addresses}), insecure=False)
    assert reason in str(exc.value)


def test_rebinding_is_caught_at_the_second_check():
    """Registration saw a public address; the send time resolver answers a
    private one. The send time check must reject."""
    first = ssrf.check_url("https://hooks.example/h", resolver=_resolver({"hooks.example": [PUBLIC]}), insecure=False)
    assert first.addresses == [PUBLIC]
    with pytest.raises(ssrf.DestinationRejected):
        ssrf.check_url("https://hooks.example/h", resolver=_resolver({"hooks.example": ["10.9.8.7"]}), insecure=False)


def test_insecure_mode_allows_loopback_http_but_never_metadata():
    checked = ssrf.check_url("http://127.0.0.1:5055/hook", resolver=_resolver({}), insecure=True)
    assert checked.addresses == ["127.0.0.1"] and checked.port == 5055
    checked = ssrf.check_url("http://localhost:8080/hook", resolver=_resolver({"localhost": ["127.0.0.1"]}), insecure=True)
    assert checked.port == 8080
    with pytest.raises(ssrf.DestinationRejected):
        ssrf.check_url("http://169.254.169.254/", resolver=_resolver({}), insecure=True)
    with pytest.raises(ssrf.DestinationRejected):
        ssrf.check_url("http://10.0.0.1/", resolver=_resolver({}), insecure=True)


def test_insecure_flag_reads_the_environment(monkeypatch):
    monkeypatch.delenv("HCA_WEBHOOK_ALLOW_INSECURE", raising=False)
    assert ssrf.allow_insecure() is False
    with pytest.raises(ssrf.DestinationRejected):
        ssrf.check_url("http://127.0.0.1:5055/hook", resolver=_resolver({}))
    monkeypatch.setenv("HCA_WEBHOOK_ALLOW_INSECURE", "1")
    assert ssrf.allow_insecure() is True
    assert ssrf.check_url("http://127.0.0.1:5055/hook", resolver=_resolver({})).port == 5055


def test_overlong_url_rejected():
    with pytest.raises(ssrf.DestinationRejected):
        ssrf.check_url("https://hooks.example/" + "a" * 2100, resolver=_resolver({}), insecure=False)
