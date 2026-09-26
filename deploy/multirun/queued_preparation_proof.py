"""(C) Copyright 2026, by Ross Richardson

Disposable real-model proof of queued preparation surviving dispatcher replacement.
Called only by run_input_proof; deliberately expires its own test lease to recover.

@author ross richardson
"""
import shutil
import time

from .artifacts import ArtifactError, write_json
from .import_quickstart import UnconfirmedVerification
from .queue_adapter import read_prepared
from .submission_adapter import SubmissionModel, PreparationAdapter, DispatchAdapter


def retire_preparation_attempt(executor, lease, output):
    """Release only this proof's stopped attempt payload; retain audit receipts.

    Called under the executor lock after the proof has resolved its published
    dataset (or abandoned the failed proof). Production retention is separate.
    The executor must confirm container removal before any mounted files vanish.
    """
    executor.cleanup(lease)
    workspace = executor.workspace(lease)
    evidence = output/'attempt-diagnostics'
    evidence.mkdir(mode=0o700,exist_ok=True)
    for name in ('identity.json','container-policy.json','container.json',
                 'create-intent.json','start-intent.json','exit.json','removed.json','stop.json'):
        if (workspace/name).is_file():
            shutil.copyfile(workspace/name,evidence/name)
    if (workspace/'execution.log').is_file():
        shutil.copyfile(workspace/'execution.log',output/'preparation.log')
    before = shutil.disk_usage(workspace).free
    for name in ('request','work'):
        path = workspace/name
        if path.is_symlink():
            raise ArtifactError('Refusing unexpected linked preparation payload')
        if path.exists():
            shutil.rmtree(path)
    # The verified dataset lives in output/artifacts, outside this attempt.
    # Preserve these observed values rather than promising reclaimed disk blocks.
    write_json(output/'preparation-cleanup.json',dict(
        temporary_payload_removed=True,free_before_bytes=before,
        free_after_bytes=shutil.disk_usage(workspace).free))


def prepare_queued(service, owner, upload_ids, *, defaults, request, jar, image, output, frontend):
    from jasmine_web.batch.docker_executor import DockerExecutor
    from jasmine_web.batch.worker import Worker
    from jasmine_web.batch.preparation import Preparations
    output.mkdir(mode=0o700)
    queue = service.queue
    releases = {'proof-release':dict(defaults=defaults,jar=jar,image=image)}
    model = SubmissionModel(releases)
    selected = service.resolve_uploads(owner,upload_ids)
    plan = model.preparation('proof-release',selected,request)
    preparations = Preparations(service)
    with queue._transaction() as (c,pool,now):
        experiment = preparations.enqueue(c,pool,now,owner,'queued-preparation-proof',image=plan['image'],
            parameters=plan['parameters'],uploads=upload_ids,resources=plan['resources'])
    # This proof specifically requires adoption of attempt 1. Queue retry policy
    # is exercised separately; a new attempt must not hide a recovery failure.
    queue.set_auto_retry(owner,queue.inspect(owner,experiment)['jobs'][0]['id'],False)
    execution = output/'execution'
    execution.mkdir(mode=0o700)
    adapter = DispatchAdapter(PreparationAdapter(service,releases,output/'artifacts'))
    executor = DockerExecutor(execution,approved_images=[image],input_roots=[execution,output/'artifacts'])
    old = Worker(queue,executor,adapter,'before-restart')
    lease = None
    try:
        with old.open():
            old.tick()
            lease = next(iter(old.leases.values()))
        # Private disposable schema only. The original dispatcher has released
        # its executor lock. Its running container must be adopted, never relaunched.
        with queue._transaction() as (c,_,now):
            c.execute("UPDATE attempts SET lease_until=%s-interval '1 second' WHERE id=%s", (now,lease.attempt_id))
        worker = Worker(queue,executor,adapter,'after-restart')
        end, progress = time.monotonic()+3700,time.monotonic()+30
        with worker.open():
            while time.monotonic()<end:
                worker.tick()
                job = queue.inspect(owner,experiment)['jobs'][0]
                if job['state'] in ('succeeded','review','cancelled'):
                    if job['state']!='succeeded' or job['attempts']!=1:
                        raise ArtifactError('Queued preparation did not succeed on its original attempt')
                    break
                if time.monotonic()>=progress:
                    print('Queued preparation continues after dispatcher restart',flush=True)
                    progress=time.monotonic()+30
                time.sleep(.5)
            else:
                raise ArtifactError('Queued preparation exceeded proof timeout')
        with queue._transaction() as (c,_,now):
            row = c.execute('SELECT result_dataset FROM preparations WHERE experiment_id=%s', (experiment,)).fetchone()
            dataset = row['result_dataset']
            resolved = preparations.resolve(c,owner,dataset,now)
        from pathlib import Path
        prepared = Path(resolved['location'])
        receipt = read_prepared(prepared)
        shutil.copyfile(prepared/'receipt.json',output/'receipt.json')
        return dict(dataset_id=dataset,prepared=prepared,receipt=receipt,dispatcher_restart=True)
    finally:
        lease = lease or next(iter(old.leases.values()),None)
        if lease is not None:
            with executor.exclusive():
                try:
                    if executor.inspect(lease)['state']!='stopped':
                        executor.stop(lease,'cancelled')
                        end=time.monotonic()+20
                        while time.monotonic()<end and executor.inspect(lease)['state']=='running':
                            time.sleep(.2)
                    retire_preparation_attempt(executor,lease,output)
                except Exception as error:
                    raise UnconfirmedVerification('Retain queued preparation state at '+str(output)) from error
