"""(C) Copyright 2026, by Ross Richardson

Private fictional data for the HTTPS rehearsal; real queue/access/download services.
No scientific simulation, real email or deployed service is used.
@author ross richardson
"""
import hashlib
import io
import json
from pathlib import Path
import sys

from deploy._workflow import frontend_path

FRONTEND = frontend_path()
sys.path[:0] = [str(FRONTEND), str(FRONTEND/'tests'/'batch')]

from browser_fixture import BrowserModel
from result_fixture import prepared_fixture, input_catalogue, result_settings, result_name
from test_visualiser import Backend, row
from jasmine_web.batch.local_database import private_directory
from jasmine_web.batch.local_executor import LocalExecutor, atomic_json
from jasmine_web.batch.output_management import OutputManagement
from jasmine_web.batch.policy import Resources, canonical
from jasmine_web.batch.results import Results, verified_file
from jasmine_web.batch.store import Queue
from jasmine_web.batch.submission_service import Submissions
from jasmine_web.batch.visualiser import Visualiser
from .local_web import parse_args
from .runtime import create_registry

RAW = b'FICTIONAL_RAW_RECORD_NOT_FOR_VISUALISER'


def digest_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def catalogue(lease, work):
    records = []
    for seed in lease.specification['seeds']:
        entries = []
        for name, file in [('Values.csv', work/f'output-{seed}.csv'),
                           ('input/options.txt', work/f'options-{seed}.txt')]:
            entries.append(dict(name=name, path=file.name, bytes=file.stat().st_size,
                                sha256=digest_file(file)))
        payload = dict(files=entries[:1], metadata=entries[1:])
        records.append(dict(seed=seed, **payload,
                            fingerprint=hashlib.sha256(canonical(payload).encode()).hexdigest()))
    return records


def targets(lease):
    return [name for seed in lease.specification['seeds']
            for name in (f'output-{seed}.csv', f'options-{seed}.txt')]


class AggregateBackend(Backend):
    """Bounded synthetic aggregation, exercising the real publication boundary."""
    identity = dict(revision='fictional-https-rehearsal')

    def process(self, sources, work, command, progress, *, comparison_set=False):
        self.calls += 1
        rows, completed = [], 0
        for source in sources:
            for entry in source['files']:
                with verified_file(self.root, entry) as stream:
                    while stream.read(1024**2):
                        pass
                completed += 1
                progress(completed)
            rows.append(row(source['configuration']['role'].lower()))
        values = dict(series=[dict(configuration=source['configuration']['id'], rows=[value])
                              for source, value in zip(sources, rows)]) if comparison_set else dict(rows=rows)
        return dict(**values, comparison_available=False, notice='Fictional proxy test; no scientific inference.')


def services(settings, dsn):
    state = private_directory(Path(settings['state']))
    queue = Queue(dsn, 'https-rehearsal', schema=settings['schema'])
    queue.migrate()
    # The shared local parser requires this test flag. Delivery still uses the
    # private callback below; the HTTPS app independently sets local_codes=False.
    args = parse_args(['serve', '--console-codes', '--state', str(state),
                       '--port', str(settings['app_port'])])
    async def deliver(email, code):
        path = state/'mail.json'
        codes = json.loads(path.read_text()) if path.exists() else {}
        codes[email] = code
        atomic_json(path, codes)
    access, datasets, preparations, _ = create_registry(args, queue, state, deliver,
                                                        capacity=Resources(8000, 16000, 32000))
    service = Submissions(access, datasets, BrowserModel())
    executor = LocalExecutor(state/'execution')
    queue.bind_executor(executor.identity())
    service.results = Results(service, executor, catalogue, name=result_name,
        inputs=input_catalogue, settings=result_settings,
        downloads=dict(capacity=2*1024**3, reserve=64*1024**2))
    service.outputs = OutputManagement(service, executor, targets)
    backend = AggregateBackend()
    backend.root, backend.public = executor.root, set(settings.get('public_datasets', []))
    service.visualiser = Visualiser(service.results, backend)
    return service, queue, executor


def bootstrap(settings, dsn):
    service, queue, executor = services(settings, dsn)
    owners = {email: service.access.approve_email(email) for email in ('alice@example.org', 'bob@example.org')}
    inputs = []
    try:
        for number in range(3):
            path = private_directory(Path(settings['state'])/('prepared-'+str(number)))
            fingerprint = prepared_fixture(path, label='fictional-'+str(number))
            if number < 2:
                upload = service.datasets.receive(owners['alice@example.org'], 'population-'+str(number)+'.csv',
                                                   io.BytesIO(RAW), expected_bytes=len(RAW))
                key = service.datasets.publish(owners['alice@example.org'], fingerprint, uploads=[upload])
            else:
                key = service.datasets.register_provider(fingerprint)
                queue.grant_dataset(owners['alice@example.org'], key)
            service.preparations.register_location(key, location=str(path), image='sha256:'+'a'*64)
            inputs.append(key)
        settings['public_datasets'] = [inputs[-1]]
        return inputs, owners
    finally:
        service.results.downloads.close()
        service.visualiser.close()


def finish(queue, executor, *, payload_mib=0):
    lease = queue.claim('fictional-https-worker')
    if lease is None:
        raise AssertionError('Fictional configuration was not claimable')
    queue.started(lease)
    workspace = executor.workspace(lease)
    workspace.mkdir(mode=0o700)
    atomic_json(workspace/'identity.json', executor._identity(lease))
    work = private_directory(workspace/'work')
    for number, seed in enumerate(lease.specification['seeds']):
        path = work/f'output-{seed}.csv'
        with path.open('wb') as output:
            output.write(b'seed,value,record\n'+seed.encode()+b',1,'+RAW+b'\n')
            if payload_mib and number == 0:
                # Exceed the unchanged 512 MiB preparation threshold, with a
                # bounded-memory high-entropy tail so the actual ZIP is sizeable.
                chunk = (b'606,1,fictional\n'*65536)[:1024**2]
                remaining = payload_mib*1024**2
                tail = min(64*1024**2, remaining)
                while remaining > tail:
                    part = chunk[:min(len(chunk), remaining-tail)]
                    output.write(part); remaining -= len(part)
                counter = 0
                while remaining:
                    part = hashlib.shake_256(('fictional-'+str(counter)).encode()).digest(512*1024).hex().encode()
                    part = part[:remaining]
                    output.write(part); remaining -= len(part); counter += 1
                output.write(b'\n')
        (work/f'options-{seed}.txt').write_text('seed='+seed+'\nconfiguration='+lease.configuration_id+'\n')
    (work/'private.log').write_bytes(RAW)
    for ordinal, record in enumerate(catalogue(lease, work)):
        queue.record_repetition(lease, ordinal, record['seed'], record['fingerprint'])
    queue.finish(lease, outcome='success', stop_evidence='f'*64)
    return lease
