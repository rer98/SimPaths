#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Tiny fictional Docker batch: retain an execution marker until released by proof.
@author ross richardson
"""
import hashlib
import json
from pathlib import Path
import time


def main():
    settings = json.loads(Path('/request/settings.json').read_text())
    with Path('/work/starts.txt').open('a') as stream:
        stream.write('started\n')
    initial = Path('/inputs/input.txt').read_bytes()
    Path('/work/input-sha256.txt').write_text(hashlib.sha256(initial).hexdigest())
    # No database, credentials, Docker socket or inbound network in this child.
    deadline = time.monotonic()+600
    while not Path('/work/finish').exists():
        if time.monotonic() > deadline:
            raise SystemExit(2)
        time.sleep(.1)
    if Path('/inputs/input.txt').read_bytes() != initial:
        raise ValueError('Fictional prepared input changed')
    values = []
    for seed in settings['seeds']:
        Path('/work/output-'+seed+'.csv').write_text('seed,value\n'+seed+',1\n')
        Path('/work/options-'+seed+'.txt').write_text('seed='+seed+'\n')
        values.append(dict(seed=seed, input_sha256=hashlib.sha256(initial).hexdigest()))
    Path('/work/results.json').write_text(json.dumps(values))


if __name__ == '__main__':
    main()
