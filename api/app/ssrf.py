"""HCA-P21a: destination URL rules for outbound webhooks.

A caller registers a destination once (app/webhook_destinations.py); the
URL passes these rules at registration and again at every send on the
address the send will actually connect to, so a DNS answer that changes
between the two moments (rebinding) cannot point the service at something
inside the network.

Rules (decision 7, docs/plans/HCA_P21A_WEBHOOK_SECURITY_v1.md):
  scheme https (http only under HCA_WEBHOOK_ALLOW_INSECURE=1, for the
  local smoke receiver; the deploy never sets it), no userinfo, a host,
  port 443 or an explicit port above 1023, the host is not a cloud metadata
  name, no IP literal or resolved address in a loopback, private, link
  local, multicast, reserved or unspecified range (IPv4 mapped, 6to4 and
  Teredo IPv6 forms are unwrapped and judged by the inner IPv4 address),
  and the host resolves to at least one address.

The resolver is injectable so tests can prove each rule without DNS.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Callable, List, Optional
from urllib.parse import urlsplit

Resolver = Callable[[str], List[str]]

METADATA_HOSTS = {
    "metadata", "metadata.google.internal", "metadata.goog",
    "instance-data", "instance-data.ec2.internal",
    "169.254.169.254", "fd00:ec2::254", "100.100.100.200",
}


class DestinationRejected(ValueError):
    """The URL fails a rule; str(exc) is the reason returned to the caller."""


@dataclass(frozen=True)
class CheckedDestination:
    url: str
    host: str
    port: int
    addresses: List[str]


def allow_insecure() -> bool:
    return os.environ.get("HCA_WEBHOOK_ALLOW_INSECURE", "") == "1"


def _system_resolver(host: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DestinationRejected(f"host does not resolve: {exc.strerror or exc}") from None
    return sorted({info[4][0] for info in infos})


_resolver: Optional[Resolver] = None


def set_resolver(resolver: Optional[Resolver]) -> None:
    """Tests install a fake; None returns to the system resolver."""
    global _resolver
    _resolver = resolver


def _inner_ipv4(addr: ipaddress.IPv6Address) -> Optional[ipaddress.IPv4Address]:
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    if addr.sixtofour is not None:
        return addr.sixtofour
    if addr.teredo is not None:
        return addr.teredo[1]
    return None


def address_is_forbidden(text: str, *, insecure: bool = False) -> Optional[str]:
    """None when the address may be connected to, else the reason."""
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return "not an IP address"
    if isinstance(addr, ipaddress.IPv6Address):
        inner = _inner_ipv4(addr)
        if inner is not None:
            reason = address_is_forbidden(str(inner), insecure=insecure)
            return f"{reason} (inside {addr})" if reason else None
        if addr.is_site_local:
            return "site local address"
    if addr.is_loopback:
        return None if insecure else "loopback address"
    if addr.is_unspecified:
        return "unspecified address"
    if addr.is_link_local:
        return "link local address"
    if addr.is_multicast:
        return "multicast address"
    if addr.is_private:
        return "private address"
    if addr.is_reserved:
        return "reserved address"
    if not addr.is_global:
        return "non global address"
    return None


def check_url(url: str, *, resolver: Optional[Resolver] = None, insecure: Optional[bool] = None) -> CheckedDestination:
    """Apply every rule; return the host, port and the addresses the send
    may connect to. Raises DestinationRejected with the first failing rule."""
    insecure = allow_insecure() if insecure is None else insecure
    resolver = resolver or _resolver or _system_resolver
    if not isinstance(url, str) or len(url) > 2048:
        raise DestinationRejected("url missing or longer than 2048 characters")
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise DestinationRejected(f"url does not parse: {exc}") from None
    if parts.scheme != "https" and not (insecure and parts.scheme == "http"):
        raise DestinationRejected("scheme must be https")
    if parts.username is not None or parts.password is not None:
        raise DestinationRejected("userinfo is not allowed in the url")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise DestinationRejected("url has no host")
    try:
        port = parts.port
    except ValueError:
        raise DestinationRejected("port is not a number") from None
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    if port != 443 and port <= 1023 and not insecure:
        raise DestinationRejected("port must be 443 or above 1023")
    if host in METADATA_HOSTS or (host.endswith(".internal") and not insecure):
        raise DestinationRejected("cloud metadata host")
    if host == "localhost" or host.endswith(".localhost"):
        if not insecure:
            raise DestinationRejected("loopback host")
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        addresses = resolver(host)
    if not addresses:
        raise DestinationRejected("host does not resolve")
    for addr in addresses:
        reason = address_is_forbidden(addr, insecure=insecure)
        if reason:
            raise DestinationRejected(f"{reason}: {addr}")
    return CheckedDestination(url=url, host=host, port=port, addresses=sorted(addresses))
