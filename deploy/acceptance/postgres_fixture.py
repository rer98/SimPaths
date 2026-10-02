"""(C) Copyright 2026, by Ross Richardson
Bounded disposable PostgreSQL for SingleRun browser acceptance; no image pulls.
Waits for the TCP server, avoiding the entrypoint's temporary bootstrap server.
@author ross richardson
"""
import os
from pathlib import Path
import secrets
import time


def start_postgres(client, model_id, port, work):
    image = client.images.get('postgres:17-alpine')
    password = secrets.token_hex(32)
    container = client.containers.run(image.id, detach=True,
        ports={'5432/tcp': ('127.0.0.1', port)}, labels={'simpaths.acceptance': model_id},
        environment=dict(POSTGRES_PASSWORD=password, POSTGRES_DB='jasmine_proof'),
        mem_limit='768m', nano_cpus=1_000_000_000, pids_limit=128,
        tmpfs={'/var/lib/postgresql/data': 'rw,size=512m'},
        command=['postgres', '-c', 'shared_buffers=32MB', '-c', 'max_wal_size=64MB',
                 '-c', 'min_wal_size=32MB', '-c', 'checkpoint_timeout=1min'])
    try:
        deadline = time.monotonic() + 60
        while container.exec_run(['pg_isready', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'jasmine_proof']).exit_code:
            if time.monotonic() >= deadline:
                raise RuntimeError('Disposable PostgreSQL did not become ready')
            time.sleep(.25)
        dsn = Path(work) / '.postgres-proof.dsn'
        fd = os.open(dsn, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(f'postgresql://postgres:{password}@127.0.0.1:{port}/jasmine_proof')
        return container, dsn
    except BaseException:
        container.reload()
        if container.labels.get('simpaths.acceptance') != model_id:
            raise RuntimeError('Disposable database cleanup identity changed') from None
        container.remove(force=True)
        raise
