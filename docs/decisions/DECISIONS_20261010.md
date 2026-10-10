# HCA design decisions (2026-10-10)

1. API key hashing stays SHA-256, no password KDF (CodeQL alert 11, rule py/weak-sensitive-data-hashing, api/app/apikeys.py). The hashed value is a server generated secret from secrets.token_urlsafe(24): 192 bits of entropy, never chosen or typed by a person. Slow hashes (scrypt, argon2, bcrypt) exist to make guessing low entropy passwords expensive; against a 192 bit random secret a stolen hash table is already unguessable, and a slow hash would only add latency to every authenticated agent call. Verification compares the hash of the presented secret in constant time. The alert is recorded as a false positive with this reasoning; the line carries a docstring pointing here. Alternative declined: scrypt or HMAC with a server pepper (adds a secret to manage, no security gain for random keys).

2. Identifier hygiene: committed files carry no personal account names, project ids or live service hostnames. Documentation uses angle bracket placeholders such as <the Cloud Run service hostname>; the extension options page and its tests use example.test hosts. The owner's shell prompt pattern in the bootstrap tooling matches any user and host.

3. Coverage gate: the API pytest job in CI reports coverage of app/ and fails under 85 percent (observed 94 percent at the time of the decision, 1415 statements). The threshold rises with the suite; it never lowers without a decision here.

4. Swift tests run in CI on a macOS runner (job swift-tests, swift test in macos/), alongside the Python, Node and gate jobs. Before this the 37 XCTests ran only on the owner's Mac.
