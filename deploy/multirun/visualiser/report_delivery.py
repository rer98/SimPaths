"""(C) Copyright 2026, by Ross Richardson
Publish a signed qualification summary to the MultiRun administrator review queue.
@author ross richardson
"""
import hashlib
import hmac
import http.client
import json
import os
import re
from urllib.parse import urlsplit


def signing_key(value):
    if not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Configure a 32-byte report key as 64 lowercase hexadecimal characters')
    return bytes.fromhex(value)


def send_report(origin, key, report):
    target = urlsplit(origin)
    if (target.scheme != 'https' or not target.hostname or target.username or target.password
            or origin != 'https://'+target.netloc):
        raise ValueError('Expected a canonical HTTPS MultiRun origin')
    payload = json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    signature = hmac.new(key,payload,hashlib.sha256).hexdigest()
    connection = http.client.HTTPSConnection(target.hostname,target.port,timeout=30)
    try:
        connection.request('POST','/admin/api/visualiser_report',
                           json.dumps(dict(report=report,signature=signature)),
                           {'Content-Type':'application/json','Origin':origin})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f'MultiRun report delivery returned HTTP {response.status}')
        receipt = json.loads(response.read(4096))
        if not re.fullmatch('[a-f0-9]{64}',receipt['candidate']):
            raise ValueError('Invalid MultiRun report receipt')
        return receipt
    finally:
        connection.close()


def main():
    env = os.environ
    if env['QUALIFICATION_RESULT'] == 'success':
        report = json.loads(env['CHECKED_REPORT'])
        if report['outcome'] not in ('technical review','scientific review','checks passed'):
            raise ValueError('Expected a qualified build')
    else:
        from .check_candidate import REFERENCE
        report = dict(outcome='failed',manifest=None,frontend=None,rows=None,changed=None,reference=REFERENCE)
    report.update(project=env['GITHUB_SHA'],visualiser=env['VISUALISER_REVISION'],
                  repository=env['GITHUB_REPOSITORY'],run=int(env['GITHUB_RUN_ID']),
                  attempt=int(env['GITHUB_RUN_ATTEMPT']))
    receipt = send_report(env['MULTIRUN_ORIGIN'],signing_key(env['REPORT_KEY']),report)
    print('MultiRun received the qualification report for '+receipt['candidate'])


if __name__ == '__main__':
    main()
