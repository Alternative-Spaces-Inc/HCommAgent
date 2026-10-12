"""Public privacy disclosure for access tokens, decisions and local/server storage.

Google access tokens are checked with Google and not persisted by HCA. Answers
can be sent to configured webhook recipients. Deployment operators supply their
actual contact details and backup/log retention policy.
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
<style>body{font-family:system-ui,sans-serif;max-width:44rem;margin:3rem auto;padding:0 1rem;line-height:1.5}</style>
</head>
<body>
<h1>Human Collections Agent: privacy policy</h1>
<p>Updated October 12, 2026. HCA is a self-hosted decision service. Your server
operator controls its deployment and access policy. Use the operator contact
provided with your installation for access, deletion and retention questions.
Source-project support is available through
<a href="https://github.com/Alternative-Spaces-Inc/HCommAgent/issues">the project issues</a>;
do not post personal decision data or credentials in public issues.</p>
<h2>Data sent and processed</h2>
<ul>
<li>The clients register a device public key, identifier, label and surface.
The private signing key remains in browser IndexedDB or the Mac login Keychain.</li>
<li>For Google linking, a Google access token is sent over HTTPS to your
selected server and checked with Google. HCA does not persist that token.
Verified account subject/email, linked client and link time are stored.</li>
<li>Signed calls read pending decisions and submit answers. The server stores
request context, options, answers, verified owner/device/surface, time and audit
information. Optional client context is untrusted metadata. Operators may also
retain network and infrastructure logs.</li>
<li>Answers and request data are delivered to the agent's configured webhook
receiver or read by the agent from the API. The operator's chosen hosting and
database providers process server data; Google processes account verification.
These recipients and providers have their own policies.</li>
</ul>
<h2>Local data and notifications</h2>
<p>Chrome stores an origin/device-scoped inbox, cursor, preferences and bounded
unsent answers. A saved offline answer is not a server-accepted decision. Mac
keeps the inbox in memory, preferences/cursor in UserDefaults and its signing key
in Keychain. Notifications hide decision details by default. Enabling previews
can expose titles and choices in system notification history or on a locked
screen. System notification cleanup remains under your control.</p>
<h2>Collection limits</h2>
<ul>
<li>No browsing history, page content, cookies or keystrokes are read by the
extension. Host access is requested for the configured API origin.</li>
<li>The clients include no advertising or tracking analytics. HCA does not
sell data; configured agent/webhook delivery and hosting are disclosed above.</li>
</ul>
<h2>Retention, export and deletion</h2>
<p>Decision and audit records remain until the operator explicitly purges them.
Administrators can export decisions in bounded pages and purge terminal records
before an explicit cutoff. Pending decisions, queued webhook deliveries and
actively leased work are preserved. Held requests remain pending; cancel or
otherwise complete them before applying a deletion policy.</p>
<p>Bounded cleanup normally retains transient events and completed jobs for
seven days, terminal webhook deliveries/attempts for thirty days, and destination
registry events for ninety days. Active work is preserved. Replay availability
is twenty-four hours and has a count bound. Owner/device profiles and destination
registrations require operator handling. Backups and infrastructure logs have
separate operator-defined retention; running-database deletion does not erase
older backups.</p>
<p>Chrome offline answers expire within twenty-four hours or an earlier non-hold
deadline, with size/attempt limits. Origin changes, key rotation and authorization
refusal clear sensitive scoped state. Uninstalling a client removes its local
application data but does not delete server records. Mac Keychain and system
notifications require separate cleanup. Ask the operator to revoke the device
and handle retained server/profile data under its published policy.</p>
</body>
</html>
"""


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy() -> HTMLResponse:
    return HTMLResponse(content=PRIVACY_HTML, status_code=200)
