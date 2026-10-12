# Dependency licenses and provenance

HCA source is licensed under Apache-2.0. Dependencies retain their own licenses
and notices in their installed distributions; the project license does not
replace those terms. The release container inventory and provenance artifact
identify the exact built image and installed versions.

The October 12, 2026 hashed runtime lock declares FastAPI, Pydantic, SQLAlchemy
and Alembic under MIT; Starlette, Uvicorn and HTTPX under BSD-3-Clause;
cryptography under Apache-2.0 OR BSD-3-Clause; and CFFI under MIT-0.
`psycopg2-binary` declares LGPL with exceptions and includes native libraries
with their own notices. Consult its installed package/license files when
redistributing an image; do not treat that dependency as Apache-licensed HCA
source. Transitive distribution notices remain part of the installed wheels.

Playwright and release/testing tools are development dependencies, excluded
from the API runtime lock. The Mac client links frameworks supplied by macOS;
it does not publish Apple SDK source. The developer application is locally
signed and must not be described as a notarized public distribution.

The signature vector's private key is deliberately public test material,
never a production device key. The Chrome manifest key identifies an
extension publicly; it is not a private credential. Synthetic screenshots and
the local mock workflow do not contain customer records.

Personal privileged operations are excluded from the current product tree.
Historical commits are retained as dated source history, not offered as a
supported product or an authenticated approval mechanism.
