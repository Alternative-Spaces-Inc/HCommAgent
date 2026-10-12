#!/usr/bin/env python3
"""Find the HTTPS no-traffic revision tag used by the candidate smoke."""
import json
import sys
import re
from urllib.parse import urlsplit

service = json.load(open(sys.argv[1]))
tag = sys.argv[2]
entries = [entry for entry in service.get('status', {}).get('traffic', []) if entry.get('tag') == tag]
if len(entries) != 1 or urlsplit(entries[0].get('url') or '').scheme != 'https':
    raise SystemExit('candidate tag is absent or has no HTTPS URL')
revision = entries[0].get('revisionName', '')
if not re.fullmatch(r'[a-z][a-z0-9-]{1,62}', revision):
    raise SystemExit('candidate revision is missing or invalid')
print(revision if '--revision' in sys.argv else entries[0]['url'])
