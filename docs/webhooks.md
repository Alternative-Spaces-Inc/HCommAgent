# Webhooks: registering a destination and verifying a delivery

HCA tells the agent that asked about every outcome of an ask (answered,
cancelled, expired) by POSTing to a webhook destination the agent registered
first. This page is the receiver's contract. Design: P21a plan
([docs/plans/HCA_P21A_WEBHOOK_SECURITY_v1.md](plans/HCA_P21A_WEBHOOK_SECURITY_v1.md)),
decisions 7 and 8 in [docs/decisions/DECISIONS_20261011.md](decisions/DECISIONS_20261011.md).

## 1 Register once

Under your api key (`Authorization: Bearer <key>`):

```
POST /v1/webhooks
{"url": "https://hooks.example.com/hca"}
```

Response 201 carries the destination `id` (`whd_...`) and the `secret`
(`whsec_...`). The secret is shown once; store it on the receiver. The URL
must pass these rules at registration and again at every send:

- https only, no userinfo, port 443 or an explicit port above 1023;
- the host must not be an IP literal in a loopback, private, link local,
  multicast, reserved or unspecified range, nor an IPv4 mapped or 6to4 IPv6
  form of one; cloud metadata host names are rejected by name;
- the host must resolve, and every resolved address must pass the same
  checks. The addresses are pinned on the row; the send re-resolves and
  connects to a checked address with the Host header and TLS SNI set to
  the registered host, so a DNS answer that changes later cannot redirect
  the POST. Redirects are not followed (a 3xx fails the attempt).

An ask whose `deliver_to.url` is not a registered, non revoked destination of
the same caller and tenant is rejected with 400
(`deliver_to.url is not a registered webhook destination`). A caller holds
at most 10 active destinations.

Other routes under the same key: `GET /v1/webhooks` (yours), `POST
/v1/webhooks/{id}/rotate` (new secret returned once; the previous secret
stays valid for 24 hours and deliveries carry a signature under both),
`DELETE /v1/webhooks/{id}` (revoke; pending deliveries to it fail dead).
Each registration, rotation and revocation writes a registry audit row.

## 2 What arrives

One JSON POST per outcome:

```
POST /hca
Content-Type: application/json
User-Agent: HCA-Webhooks/1
X-HCA-Delivery-Id: whdl_5a1f...        stable across retries; deduplicate on it
X-HCA-Timestamp: 1760230000            unix seconds when this attempt was signed
X-HCA-Event: request.answered          or request.cancelled, request.expired
X-HCA-Request-Id: 486c9196c8c7...
X-HCA-Signature: t=1760230000,v1=3f9c...   one v1 per valid secret (two during a rotation overlap)

{"answer":{...},"event":"request.answered","request_id":"486c...","sent_at":"2026-10-11T23:26:40+00:00","state":"answered"}
```

`answer` is the answer record (control, value, answered_by, surface, device,
answered_at, path_taken, duration_ms, passthrough); it is `null` for a plain
expiry with no answer recorded. The body is compact JSON with sorted keys;
verify the raw bytes you received, never a re-serialised copy.

## 3 Verify

`v1` is HMAC-SHA256 under your secret over the string `<t>.<raw body>`
(timestamp, a dot, the exact request body bytes). Accept when any `v1`
matches and `t` is within 5 minutes of your clock, then deduplicate on
`X-HCA-Delivery-Id` and answer 2xx within 10 seconds. Any other status, a
timeout or a redirect counts as a failed attempt.

Python (standard library only):

```python
import hashlib, hmac, time

def verify(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    ts, sigs = None, []
    for item in header.split(","):
        k, _, v = item.strip().partition("=")
        if k == "t":
            ts = int(v)
        elif k == "v1":
            sigs.append(v)
    if ts is None or not sigs or abs(int(time.time()) - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)
```

Node:

```js
const crypto = require("crypto");

function verify(secret, header, rawBody, tolerance = 300) {
  const parts = Object.create(null);
  const sigs = [];
  for (const item of header.split(",")) {
    const [k, v] = item.trim().split("=");
    if (k === "t") parts.t = Number(v);
    else if (k === "v1") sigs.push(v);
  }
  if (!parts.t || sigs.length === 0 || Math.abs(Date.now() / 1000 - parts.t) > tolerance) return false;
  const expected = crypto.createHmac("sha256", secret).update(`${parts.t}.`).update(rawBody).digest("hex");
  return sigs.some((s) => s.length === expected.length && crypto.timingSafeEqual(Buffer.from(s, "hex"), Buffer.from(expected, "hex")));
}
```

The same check lives in `api/app/webhooks.py` (`verify_signature`) and in
`scripts/smoke_golden_path.py`, which verifies every delivery the pipeline's
smoke receiver gets.

## 4 Retries and the dead state

The outbox is durable: the delivery row is written in the same database
transaction as the state change, and any API instance's job worker sends
it. A failed attempt is retried after 1 s, 5 s, 30 s, 2 minutes, 10 minutes,
30 minutes and 1 hour (each with up to 20 percent jitter). After the 7th
failure the delivery is `dead`; a `delivery.dead` row in the destination's
registry audit records the reason. Every HTTP attempt is a row in
`webhook_attempts` (status code or error, timestamp), so "we never got it"
is answered from the table. `GET /v1/internal/metrics` (admin bearer)
reports `webhooks.queued`, `delivered`, `dead` and the oldest queued age.

## 5 How secrets are kept

The server must sign, so it keeps each destination secret encrypted with
AES-256-GCM under a key derived (HKDF-SHA256) from the api key pepper held
in Secret Manager; the destination id is the associated data (decision 8).
No webhook secret is logged or returned after registration or rotation. If
the pepper is ever rotated, rotate every destination afterwards: deliveries
to a destination whose secret no longer decrypts fail dead with the reason
recorded.
