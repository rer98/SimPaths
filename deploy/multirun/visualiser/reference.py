"""(C) Copyright 2026, by Ross Richardson
Transfer the verified installation's manifest and exact scientific calculator to qualification.
@author ross richardson
"""
import argparse
import base64
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from ..visualiser_editions import REFERENCE_FILES, read_reference


def fetch_file(origin, key, name, expected):
    target = urlsplit(origin)
    if target.scheme != 'https' or not target.hostname or target.username or target.password or origin != 'https://'+target.netloc:
        raise ValueError('Expected a canonical HTTPS MultiRun origin')
    issued = int(time.time())
    signature = hmac.digest(key, f'visualiser-reference-v1:{issued}:{name}:{expected}'.encode(), 'sha256').hex()
    conn = http.client.HTTPSConnection(target.hostname, target.port, timeout=45)
    try:
        conn.request('POST', '/admin/api/visualiser_reference', json.dumps(dict(issued=issued, name=name, expected=expected, signature=signature)),
                     {'Content-Type':'application/json','Origin':origin})
        reply = conn.getresponse()
        if reply.status != 200:
            raise RuntimeError(f'MultiRun could not supply its verified reference (HTTP {reply.status})')
        payload = reply.read(16*1024**2+1)
        if len(payload) > 16*1024**2:
            raise ValueError('Reference response exceeds the transfer limit')
    finally:
        conn.close()
    result = json.loads(payload)
    if not re.fullmatch('[0-9a-f]{64}', result['manifest']) or expected and expected != result['manifest']:
        raise ValueError('Reference manifest changed during selection')
    return base64.b64decode(result['data'], validate=True), result['manifest']


def fetch(origin, key, directory):
    directory.mkdir()
    expected = ''
    for name in REFERENCE_FILES:
        data, expected = fetch_file(origin, key, name, expected)
        (directory/name).write_bytes(data)
    manifest, digest, _ = read_reference(directory)
    if digest != expected:
        raise ValueError('Reference manifest checksum differs from the installation receipt')
    return manifest['revision'], digest


if __name__ == '__main__':
    from .report_delivery import signing_key
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    directory = parser.parse_args().directory
    if os.environ.get('MULTIRUN_ORIGIN'):
        revision, digest = fetch(os.environ['MULTIRUN_ORIGIN'], signing_key(os.environ['REPORT_KEY']), directory)
    else:
        # Disconnected CI can exercise the pipeline with an explicitly identified archive.
        from ..visualiser_editions import REGISTRY
        available = [row for row in json.loads(REGISTRY.read_bytes()) if not row.get('retired')]
        if not available:
            raise ValueError('Configure a verified installation as the reference. All offline archives have retired.')
        row = available[-1]
        archive = REGISTRY.parent/row['revision'][:8]
        manifest, digest, files = read_reference(archive)
        if digest != row['manifest_sha256'] or manifest['revision'] != row['revision']:
            raise ValueError('Offline reference differs from the approved archive')
        directory.mkdir()
        for name, body in files.items():
            (directory/name).write_bytes(body)
        revision = manifest['revision']
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        print(f'revision={revision}\nmanifest={digest}', file=output)
