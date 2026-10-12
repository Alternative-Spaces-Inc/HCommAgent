# Webhook receiver contract

HCA delivers terminal request outcomes to an agent's registered webhook destination. The outbox is durable, but deliveries can repeat after an ambiguous network failure or a worker restart. Authenticate each delivery and deduplicate the business effect transactionally. A successful HTTP response records transport acknowledgement; it does not prove the receiver performed the intended business operation.

## Register a destination

Use the caller's API key to `POST /v1/webhooks` with `{"url":"https://hooks.example.com/hca"}`. The response contains a destination ID and a signing secret shown once. Store the secret securely on the receiver. Use the registered URL in `deliver_to` for a request; unregistered, revoked or different-caller/tenant destinations are refused. Each caller can hold at most ten active destinations.

Production requires HTTPS, no credentials in the URL, an allowed port, and a publicly routable destination. IP literals, cloud metadata names, loopback/private/link-local/reserved addresses and unsafe IPv6 forms are rejected. Every DNS result must pass the checks at registration and send. The sender connects to a checked address while retaining the registered Host/TLS identity. Redirects are refused. A development-only switch permits a loopback mock receiver and is prohibited in production.

`GET /v1/webhooks` lists the caller's registry. `POST /v1/webhooks/{id}/rotate` returns a new secret, with the previous secret accepted for a 24-hour overlap. `DELETE /v1/webhooks/{id}` revokes the destination; pending deliveries cannot keep using it. Registry changes are audited.

## Payload and authenticated identity

The POST includes:

```text
Content-Type: application/json
User-Agent: HCA-Webhooks/1
X-HCA-Delivery-Id: <stable delivery ID>
X-HCA-Timestamp: <attempt timestamp>
X-HCA-Event: request.answered
X-HCA-Request-Id: <request ID>
X-HCA-Signature: t=<timestamp>,v1=<HMAC hex>
```

The compact JSON body contains `delivery_id`, `event`, `request_id`, `state`, `answer` and `sent_at`. `answer` is null when no answer exists. The same delivery ID remains stable across retries; each attempt has a fresh signature timestamp. During signing-secret overlap there can be two `v1` values.

The signature is HMAC-SHA256 under the signing secret over `<timestamp>.<raw body bytes>`. `delivery_id` is inside those authenticated bytes. Verify raw bytes before trusting the parsed payload, and require its `delivery_id` to equal `X-HCA-Delivery-Id`. Use the signed body's event/request ID as authoritative; convenience headers must match if your application relies on them. Do not deduplicate on an unchecked header.

### Python verification

```python
import hashlib
import hmac
import json
import re
import time


def verify(secret, header, raw_body, delivery_id, tolerance=300):
    if not delivery_id or len(raw_body) > 1024 * 1024 or len(header) > 4096:
        return False
    timestamps, signatures = [], []
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        if key == "t":
            timestamps.append(value)
        elif key == "v1" and re.fullmatch(r"[0-9a-f]{64}", value):
            signatures.append(value)
    if len(timestamps) != 1 or not re.fullmatch(r"[0-9]{1,12}", timestamps[0]):
        return False
    timestamp = int(timestamps[0])
    if not signatures or abs(int(time.time()) - timestamp) > tolerance:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, value) for value in signatures):
        return False
    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("delivery_id") == delivery_id
```

### Node verification

```js
import crypto from "node:crypto";

function verify(secret, header, rawBody, deliveryId, tolerance = 300) {
  if (!deliveryId || rawBody.length > 1024 * 1024 || header.length > 4096) return false;
  const timestamps = [], signatures = [];
  for (const item of header.split(",")) {
    const index = item.indexOf("=");
    const key = item.slice(0, index).trim();
    const value = item.slice(index + 1).trim();
    if (key === "t") timestamps.push(value);
    if (key === "v1" && /^[0-9a-f]{64}$/.test(value)) signatures.push(value);
  }
  if (timestamps.length !== 1 || !/^[0-9]{1,12}$/.test(timestamps[0])) return false;
  const timestamp = Number(timestamps[0]);
  if (!signatures.length || Math.abs(Date.now() / 1000 - timestamp) > tolerance) return false;
  const expected = crypto.createHmac("sha256", secret)
    .update(`${timestamp}.`).update(rawBody).digest();
  if (!signatures.some((value) => crypto.timingSafeEqual(expected, Buffer.from(value, "hex")))) return false;
  try {
    const payload = JSON.parse(rawBody.toString("utf8"));
    return payload !== null && !Array.isArray(payload) && payload.delivery_id === deliveryId;
  } catch { return false; }
}
```

The server reference verifier is `api/app/webhooks.py:verify_signature`, which requires the received `delivery_id` argument. Align both parties' clocks. The default tolerance is five minutes. Bound incoming request/header sizes before invoking the verifier and handle verification failure without performing an effect.

## Deduplicate the effect

After verification, insert the authenticated delivery ID under a database uniqueness constraint in the same receiver transaction as the business effect. A duplicate should return a successful acknowledgement without repeating the effect. Preserve that record long enough for delayed retries and any operator recovery/re-drive policy.

Do not acknowledge before durable acceptance. If the effect calls another nontransactional system, use your own durable outbox and idempotent downstream operation; a check followed by a separate external side effect can still race or crash. The smoke receiver demonstrates signature agreement; the example below demonstrates a durable local receiver transaction.

For a local SQLite receiver, create both tables once, then process a verified payload in one transaction:

```python
import json
import sqlite3

# Provision once. Use the same database for the ledger and business effect.
with sqlite3.connect("receiver.db") as db:
    db.execute("CREATE TABLE IF NOT EXISTS received (delivery_id TEXT PRIMARY KEY)")
    db.execute("CREATE TABLE IF NOT EXISTS outcomes (delivery_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

def accept_verified(raw_body, delivery_id):
    # Invoke only after verify_signature(..., delivery_id=delivery_id).
    with sqlite3.connect("receiver.db") as db:
        db.execute("BEGIN IMMEDIATE")
        inserted = db.execute("INSERT OR IGNORE INTO received VALUES (?)", (delivery_id,)).rowcount
        if inserted:
            db.execute("INSERT INTO outcomes VALUES (?, ?)",
                       (delivery_id, json.dumps(json.loads(raw_body))))
    # Return 2xx only after this transaction commits. Exceptions roll back
    # both records; an authenticated retry can safely attempt again.
```

## Deadline, retries and dead deliveries

A delivery attempt has a ten-second overall deadline, including DNS and network work, with a shorter connect budget. DNS work is bounded; redirects fail; response consumption cannot extend the operation indefinitely. Return a successful status promptly after durable acceptance. Any other status, timeout, transport failure or redirect is a failed attempt.

The default schedule has **eight total attempts** and seven waits: 1, 5, 30, 120, 600, 1,800 and 3,600 seconds, each with up to 20 percent jitter. The nominal waits sum to **6,156 seconds (1 hour, 42 minutes, 36 seconds)**. Network attempt time, worker availability and scheduling add to that duration; this is not a one-hour delivery SLA. The retry schedule is configurable, so an operator must calculate their actual horizon and receiver dedupe retention.

After the final failed attempt, the delivery is `dead` and records its reason in the destination registry audit. Each attempt has a delivery-linked record with status/error/time. `GET /v1/internal/metrics`, protected by the admin bearer, reports queued/delivered/dead counts and queued age. A database lease prevents stale completion writes, but cannot prevent a receiver from seeing a duplicate POST.

There is no public promise of automatic dead-letter re-drive. Investigate the receiver and recorded error, repair/re-register or rotate when appropriate, reconcile the receiver's authenticated delivery ledger, and use a separately reviewed recovery procedure. Recreating effects without checking that ledger risks duplication. Live alert thresholds and re-drive evidence belong to the operator runbook.

## Secret encryption and rotation

This focused remediation retains the existing encryption format: AES-256-GCM with a key derived by HKDF from the API-key pepper and the destination ID as associated data. Changing that pepper can make retained destinations undecryptable. Dedicated versioned encryption keys and their migration are tracked separately from review items 1 to 12. Keep existing encryption material until that migration is validated.
