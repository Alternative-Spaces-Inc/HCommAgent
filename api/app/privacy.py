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
<p>The Human Collections Agent (HCA) browser extension and menu bar app
present decisions to the owner of this server and send the owner's
answers back to it. This server is operated by the owner for the
owner's own use.</p>
<h2>What the extension sends to this server</h2>
<ul>
<li>Once, at registration: a public key the extension generates on the
owner's device. The private half never leaves the device.</li>
<li>Once, at sign-in: a Google ID token (email and account id) so the
server can confirm the device belongs to a listed owner account. The
server keeps the verified email address on the device record and does
not keep the token.</li>
<li>Afterwards: signed requests to read pending decisions and to record
the owner's answers. Each answer is stored with the time, the device
that gave it and the surface it was given on.</li>
</ul>
<h2>What it does not do</h2>
<ul>
<li>No browsing history, page content, cookies or keystrokes are read or
sent. Site access is requested only for this server's own address.</li>
<li>Nothing is sold, shared with third parties or used for advertising.</li>
<li>No analytics or tracking code runs in the extension.</li>
</ul>
<h2>Retention and contact</h2>
<p>Decision records stay on this server as the owner's audit trail until
the owner deletes them. The owner can revoke a device at any time, after
which its requests are refused. Questions go to the operator of this
server through the channel that gave you the install link.</p>
</body>
</html>
"""


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy() -> HTMLResponse:
    return HTMLResponse(content=PRIVACY_HTML, status_code=200)
