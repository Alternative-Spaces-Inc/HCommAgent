"""HCA-P17: GET /privacy, the privacy policy page the Chrome Web Store listing
links to. Static HTML, no database, no auth. The text describes what the
browser extension (and the macOS app, same contract) sends to this server:
a device public key once, the owner's Google ID token once at link time,
and signed requests afterwards. Nothing else leaves the owner's machine
and nothing is shared or sold. Keep this page in step with
docs/runbooks/HCA_CHROME_WEB_STORE.md (the listing's justifications quote it).
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

PRIVACY_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Human Collections Agent: privacy policy</title>
<style>body{font-family:system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem;line-height:1.5}</style>
</head>
<body>
<h1>Human Collections Agent: privacy policy</h1>
<p>Updated October 12, 2026. HCA is a self-hosted decision service. The operator
who supplies your server address controls that deployment and its data. Contact
that operator for access or deletion. Source project support is available through
<a href="https://github.com/Alternative-Spaces-Inc/HCommAgent">the repository</a>;
report vulnerabilities privately through its Security tab.</p>
<h2>Data collected and used</h2>
<ul>
<li>The clients generate a device key. Only its public key is registered with
the server; the private key remains in browser storage or the macOS Keychain.</li>
<li>At Google sign-in, a Google access token is sent over HTTPS to the selected
server and checked with Google. The token is not persisted by HCA. Verified
Google subject, email, device identity, and linking time are stored.</li>
<li>Agent-provided questions and your decisions are stored with timestamps and
verified device/owner identity. Client-provided descriptions are identified as
untrusted metadata. Server operators may also retain network access logs.</li>
<li>The extension stores an origin-scoped inbox, event cursor, preferences, and
bounded offline answers locally. Offline answers expire after the documented
client queue lifetime; configuration changes clear the old server's state.</li>
</ul>
<h2>Recipients and permissions</h2>
<p>Answers are delivered to webhook recipients registered by the asking agent,
or read by that agent from the API. The operator's hosting provider processes
server data; Google processes sign-in verification. HCA does not sell data or
use it for advertising. No browsing history, page content, cookies, or keystrokes
are read. Browser host access is granted only to the chosen API origin. HCA's
use of Google user data follows the Chrome Web Store User Data Policy, including
Limited Use requirements.</p>
<h2>Retention, export, and deletion</h2>
<p>Decision and audit records remain until the operator explicitly deletes them.
The admin API provides paginated export and bounded deletion of completed
records with an explicit tenant confirmation. Active decisions are retained;
the operator must cancel them before deletion. Transient event replay and
completed job rows older than seven days are cleaned by the authenticated
scheduler sweep. Replay availability is 24 hours and may be limited by count.</p>
<p>Device/owner profiles and registered webhook configuration remain until the
operator removes them. Database backups and infrastructure logs have a separate
operator-defined retention period. Deletion from a running database does not
immediately erase older backups; the operator must communicate and enforce its
backup policy. Revoked devices lose server access and clients clear stale state
when they learn authorization has been revoked.</p>
<h2>Notifications and your choices</h2>
<p>System notifications may appear on a lock screen. Chrome uses redacted
notifications by default. Review sensitive content in the authenticated client.
You choose the API origin and can revoke browser permissions or uninstall the
client. Ask the operator to revoke the device and remove server-side records.</p>
</body>
</html>
"""


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy() -> HTMLResponse:
    return HTMLResponse(content=PRIVACY_HTML, status_code=200)
