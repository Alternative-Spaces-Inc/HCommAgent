"""Google sign-in on top of the device key (HCA-P7, plan section 4).

The extension obtains an OAuth access token for its own Chrome Extension
OAuth client through chrome.identity.getAuthToken and posts it, over a
device-signed call, to POST /v1/devices/self/google-link. This module
turns that token into a verified Google identity or a reason to refuse.

Verification goes to Google's tokeninfo endpoint
(https://oauth2.googleapis.com/tokeninfo?access_token=...), which answers
with the client the token was minted for (aud / azp), the account (sub,
email, email_verified) and the remaining lifetime. The token is accepted
only when

  - aud or azp is one of the configured client ids: HCA_GOOGLE_CLIENT_ID
    (the Chrome Extension OAuth client, kept as an alias) plus every id in
    HCA_GOOGLE_CLIENT_IDS (comma separated; HCA-P13 adds the Desktop client
    the macOS app uses). A token minted for any other app is refused, so a
    stolen token from another site cannot link a device,
  - email_verified is true,
  - expires_in is positive,
  - and, when HCA_OWNER_EMAILS is set (comma separated), the email is in
    that list; the owner list is the allow list for who may answer asks.

No configured client id at all fails closed: linking is refused with 503
so a deployment without client ids cannot accept any account.

The HTTP call is injectable (set_fetcher) so tests never reach Google.
"""

import os
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


@dataclass
class GoogleIdentity:
    sub: str
    email: str


class GoogleAuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _default_fetch(access_token: str) -> tuple[int, dict]:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(TOKENINFO_URL, params={"access_token": access_token})
    try:
        body = r.json()
    except Exception:
        body = {}
    return r.status_code, body if isinstance(body, dict) else {}


_fetcher: Callable[[str], tuple[int, dict]] = _default_fetch


def set_fetcher(fn: Optional[Callable[[str], tuple[int, dict]]]) -> None:
    """Tests install a fake tokeninfo; None restores the real one."""
    global _fetcher
    _fetcher = fn or _default_fetch


def client_id() -> str:
    return os.environ.get("HCA_GOOGLE_CLIENT_ID", "").strip()


def client_ids() -> set[str]:
    """Every OAuth client whose tokens may link a device (HCA-P13)."""
    ids = {client_id()}
    raw = os.environ.get("HCA_GOOGLE_CLIENT_IDS", "")
    ids.update(c.strip() for c in raw.split(","))
    ids.discard("")
    return ids


def owner_emails() -> set[str]:
    raw = os.environ.get("HCA_OWNER_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def verify_access_token(access_token: str) -> GoogleIdentity:
    expected = client_ids()
    if not expected:
        raise GoogleAuthError(503, "google client id is not configured")
    token = (access_token or "").strip()
    if not token:
        raise GoogleAuthError(422, "access_token is required")

    status, info = _fetcher(token)
    if status != 200 or not info:
        raise GoogleAuthError(401, "google token did not verify")

    audience = {str(info.get("aud", "")), str(info.get("azp", ""))}
    if not (expected & audience):
        raise GoogleAuthError(403, "google token was issued to another client")

    verified = info.get("email_verified")
    if verified not in (True, "true", "1", 1):
        raise GoogleAuthError(403, "google account email is not verified")

    try:
        expires_in = int(info.get("expires_in", 0))
    except (TypeError, ValueError):
        expires_in = 0
    if expires_in <= 0:
        raise GoogleAuthError(401, "google token has expired")

    sub = str(info.get("sub", "")).strip()
    email = str(info.get("email", "")).strip().lower()
    if not sub or not email:
        raise GoogleAuthError(401, "google token carries no account")

    allowed = owner_emails()
    if allowed and email not in allowed:
        raise GoogleAuthError(403, "google account is not an owner of this deployment")

    return GoogleIdentity(sub=sub, email=email)
