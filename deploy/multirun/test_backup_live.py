"""(C) Copyright 2026, by Ross Richardson

Online recovery acceptance with fictional SQL rows and immutable native outputs.
No scientific model or email is launched. Shared SingleRun exports are synthetic.
@author ross richardson
"""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import io
import os
from pathlib import Path
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from . import backup, backup_live, backup_single
from .artifacts import ArtifactError
from .backup_files import file_hash
from .maintenance import GATE, service_state
from .releases import atomic_json
from . import test_backup as fixtures
from .test_backup import DSN, IMAGE


@unittest.skipUnless(DSN,'Use backup_proof.py with disposable PostgreSQL')
class LiveBackupTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.BackupRestoreTests(); self.addCleanup(self.f.doCleanups); self.f.setUp()
        self.q=self.f.q; self.state=self.f.state
        self.q.backup_state=self.state

    def active(self):
        lease=self.q.claim('online-writer'); self.q.started(lease)
        work=self.f.executor.workspace(lease)/'work'; work.mkdir(mode=0o700,parents=True)
        (work/'mutable-person.csv').write_bytes(b'partial raw simulation output')
        self.f.f.sql("UPDATE attempts SET created_at=clock_timestamp()-interval '5 seconds',deadline=clock_timestamp()+interval '55 seconds' WHERE id=%s",(lease.attempt_id,))
        return lease

    def save(self, **kwargs):
        with service_state(self.state):
            return backup_live.create(self.q,self.state,self.f.backup,self.f.tools,**kwargs)

    def restore(self, **kwargs):
        return backup.restore(self.f.target_q,self.f.backup,self.f.target,self.f.target_tools,**kwargs)

    def test_running_attempt_new_publication_and_heartbeat_continue_while_snapshot_stays_fixed(self):
        from jasmine_web.batch.backup_guard import BackupBusy
        from jasmine_web.batch.backup import table_inventory
        lease=self.active()
        original=self.f.f.sql('SELECT attempts,spent_seconds FROM jobs WHERE id=%s',(lease.job_id,))[0]
        with self.q._connection() as c: before=table_inventory(c,self.q)
        def during():
            self.q.heartbeat(lease)
            self.f.f.sql("UPDATE jobs SET input_issue='after exported snapshot' WHERE id=%s",(lease.job_id,))
            (self.state/'uploads/new-after-snapshot').write_bytes(b'new unreferenced upload')
            (self.f.executor.workspace(lease)/'work/mutable-person.csv').write_bytes(b'model continues writing')
            with self.assertRaises(BackupBusy): self.f.datasets.clear_uploads(self.f.owner,[])
        self.save(after_snapshot=during)
        manifest=backup.verify(self.f.backup)
        self.assertEqual(before,manifest['database'])
        self.assertNotIn('uploads/new-after-snapshot',manifest['state_tree']['files'])
        self.assertFalse(any(lease.execution_key in k for k in manifest['state_tree']['files']))
        self.assertEqual(original,self.f.f.sql('SELECT attempts,spent_seconds FROM jobs WHERE id=%s',(lease.job_id,))[0])
        self.restore()
        from psycopg import sql
        with self.f.target_q._connection() as c:
            row=c.execute('SELECT * FROM jobs WHERE id=%s',(lease.job_id,)).fetchone()
            self.assertEqual('retry_wait',row['state']); self.assertEqual(1,row['attempts'])
            self.assertGreaterEqual(row['spent_seconds'],5); self.assertLess(row['spent_seconds'],20)
            reps=c.execute('SELECT * FROM repetitions WHERE attempt_id=%s',(lease.attempt_id,)).fetchall()
            self.assertEqual(['606'],[r['expected_seed'] for r in reps])
            self.assertTrue(all(r['actual_seed'] is None for r in reps))
            self.assertEqual(0,c.execute('SELECT count(*) AS n FROM reservations WHERE released_at IS NULL').fetchone()['n'])
        with self.assertRaises(ArtifactError): backup.activate(self.f.target_q,self.f.backup,self.f.target,image_check=lambda _:None)
        with service_state(self.state),self.assertRaises(ArtifactError):
            backup.activate(self.f.target_q,self.f.backup,self.f.target,image_check=lambda _:None,source_isolated=True)
        backup.activate(self.f.target_q,self.f.backup,self.f.target,image_check=lambda _:None,source_isolated=True)
        self.assertFalse((self.f.target/GATE).exists())
        next_attempt=self.f.target_q.claim('recovered-worker')
        self.assertIsNotNone(next_attempt)
        self.assertEqual(lease.job_id,next_attempt.job_id); self.assertNotEqual(lease.attempt_id,next_attempt.attempt_id)
        self.assertEqual(lease.specification['seeds'],next_attempt.specification['seeds'])

    def test_interrupted_restore_is_idempotent_and_never_charges_offline_time(self):
        from jasmine_web.batch.backup import table_inventory
        self.active(); self.save()
        def interrupt(): raise RuntimeError('fictional interruption after native dump commit')
        with self.assertRaises(RuntimeError): self.restore(after_database=interrupt)
        self.restore(resume=True)
        with self.f.target_q._connection() as c: before=table_inventory(c,self.f.target_q)
        self.restore(resume=True)
        with self.f.target_q._connection() as c: self.assertEqual(before,table_inventory(c,self.f.target_q))

    def test_confirmed_growth_survives_restore_without_replaying_pending_changes(self):
        from jasmine_web.batch.policy import Resources
        from jasmine_web.batch.resource_recovery import RecoveryPolicy, effective
        self.q.cancel(self.f.owner,self.f.f.job(self.f.owner,self.f.queued)['id'])
        run=dict(id='baseline',parameters=self.f.configuration.editable_configuration())
        self.q.submit(self.f.owner,uuid4().hex,label='Growing recovery fixture',model_digest=IMAGE,
            dataset_id=self.f.dataset,seed_plan=['606'],run_sets=[run],resources=Resources(2000,4000,8000),
            resource_recovery=RecoveryPolicy(),heap_limits={'baseline':2048})
        lease=self.active()
        changed=self.q.reserve_resources(lease,'storage')
        lease=self.q.confirm_resources(changed,changed.pending_change['target_resources'])
        self.assertEqual(effective(lease)['storage_mib'],10000)
        pending=self.q.reserve_resources(lease,'memory')
        self.assertIsNotNone(pending.pending_change)
        source=self.f.f.sql('SELECT state,target_resources FROM resource_changes ORDER BY revision')
        self.save(); self.restore()
        with self.f.target_q._connection() as c:
            self.assertEqual(['confirmed','aborted'],[r['state'] for r in c.execute(
                'SELECT state FROM resource_changes ORDER BY revision').fetchall()])
            job=c.execute('SELECT next_resources,next_heap_mib,attempts FROM jobs WHERE id=%s',
                          (lease.job_id,)).fetchone()
            self.assertEqual(job['next_resources'],effective(lease))
            self.assertEqual(job['next_heap_mib'],2048); self.assertEqual(job['attempts'],1)
            self.assertEqual(0,c.execute("SELECT count(*) AS n FROM resource_changes WHERE state='pending'").fetchone()['n'])
        self.assertEqual(source,self.f.f.sql('SELECT state,target_resources FROM resource_changes ORDER BY revision'))
        backup.activate(self.f.target_q,self.f.backup,self.f.target,image_check=lambda _:None,source_isolated=True)
        retry=self.f.target_q.claim('restored-worker')
        self.assertEqual(retry.job_id,lease.job_id)
        self.assertEqual(retry.resources,effective(lease)); self.assertEqual(retry.heap_mib,lease.heap_mib)
        self.assertEqual(retry.specification,lease.specification)
        self.assertIsNone(retry.pending_change)

    def test_cancel_retry_opt_out_exhaustion_and_revocation_remain_bounded(self):
        for case,expected in [('cancel','cancelled'),('opt-out','review'),('exhausted','review'),('revoked','review')]:
            with self.subTest(case=case):
                f=LiveBackupTests(); f.setUp(); self.addCleanup(f.doCleanups)
                lease=f.active()
                if case=='cancel': f.q.cancel(f.f.owner,lease.job_id)
                elif case=='opt-out': f.f.f.sql('UPDATE jobs SET auto_retry=false WHERE id=%s',(lease.job_id,))
                elif case=='exhausted': f.f.f.sql('UPDATE jobs SET spent_seconds=180 WHERE id=%s',(lease.job_id,))
                else: f.f.f.sql("UPDATE dataset_grants SET valid_until=clock_timestamp()-interval '1 second' WHERE dataset_id=%s",(f.f.dataset,))
                f.save(); f.restore()
                with f.f.target_q._connection() as c:
                    row=c.execute('SELECT state,attempts FROM jobs WHERE id=%s',(lease.job_id,)).fetchone()
                    self.assertEqual(expected,row['state']); self.assertEqual(1,row['attempts'])

    def test_receiving_uploads_are_excluded_and_only_target_receipts_mark_failed(self):
        self.active()
        self.f.f.sql("INSERT INTO uploads(id,pool_id,owner_id,name,reserved_bytes,state) VALUES ('receiving-test',%s,%s,'private.csv',10,'receiving')",(self.q.pool_id,self.f.owner))
        (self.state/'uploads/receiving-test').write_bytes(b'partial')
        self.save(); self.restore()
        with self.f.target_q._connection() as c:
            self.assertEqual('failed',c.execute("SELECT state FROM uploads WHERE id='receiving-test'").fetchone()['state'])
        self.assertEqual('receiving',self.f.f.sql("SELECT state FROM uploads WHERE id='receiving-test'")[0]['state'])
        self.assertNotIn('uploads/receiving-test',backup.verify(self.f.backup)['state_tree']['files'])

    def test_altered_retained_upload_cannot_publish_a_recovery_point(self):
        upload=self.f.f.sql("SELECT id FROM uploads WHERE state='ready'")[0]['id']
        (self.state/'uploads'/upload).chmod(0o600); (self.state/'uploads'/upload).write_bytes(b'changed')
        with self.assertRaises(ArtifactError): self.save()
        self.assertFalse(self.f.backup.exists())

    def test_shared_single_run_ownership_saved_exports_and_inactive_gate_survive_native_restore(self):
        from jasmine_web.vm_state import PostgresVMState, VMStateUnavailable
        from psycopg import sql
        schema='vm_backup_'+uuid4().hex[:12]
        shared=dict(pool_id=self.q.pool_id,schema=self.q.schema)
        vm=PostgresVMState(self.q.dsn,schema=schema,shared_pool=shared); self.addCleanup(vm.close); vm.migrate()
        with self.q._connection() as c:
            self.addCleanup(self.f.f.sql,'DROP SCHEMA '+schema+' CASCADE')
        sid=str(uuid4())
        vm.reserve_session(sid,dict(model_id='fictional',status='starting',memory_bytes=1024,admission_owner='alice',backend_secret='private',
            resources=dict(cpu_millis=1,memory_mib=1,storage_mib=1)),
            limit=10,memory_budget=1024**3,per_client=10,containers=lambda:[])
        original=vm.deployment_id()
        def capture(c,dsn,s,destination,source):
            directory=f'singlerun/{s}/'+hashlib.sha256(sid.encode()).hexdigest()
            root=destination/directory; root.mkdir(mode=0o700,parents=True)
            atomic_json(root/'parameters.json',{'seed':606,'endYear':2026})
            output=root/'output-0000.zip'
            with zipfile.ZipFile(output,'x') as archive: archive.writestr('saved/csv/Person.csv','fictional aggregate test')
            output.chmod(0o400)
            return dict(images=[IMAGE],sessions=[dict(id=sid,directory=directory,archives=[dict(id='output-0000',timestamp='closed-run',file=directory+'/output-0000.zip',**file_hash(root,output.name))],
                parameters=directory+'/parameters.json',active_output_omitted=True,download_allowed=True,expires_at=None)])
        self.active(); self.save(single_capture=capture); self.restore()
        restored=PostgresVMState(self.f.target_q.dsn,schema=schema,shared_pool=shared); self.addCleanup(restored.close)
        with self.assertRaises(VMStateUnavailable): restored.migrate()
        backup.activate(self.f.target_q,self.f.backup,self.f.target,image_check=lambda _:None,source_isolated=True)
        restored.migrate()
        record=restored.peek_session(sid)
        self.assertEqual('failed',record['status']); self.assertNotIn('backend_secret',record)
        self.assertNotEqual(original,restored.deployment_id())
        self.assertEqual(original,vm.deployment_id()); self.assertEqual('starting',vm.peek_session(sid)['status'])
        from jasmine_web.vm_recovery import download,expire_recovery
        response=download(record,'output-0000')
        self.assertEqual(200,response.status_code); response._saved_file.close()
        denied={**record,'recovery':{**record['recovery'],'download_allowed':False}}
        self.assertEqual(404,download(denied,'output-0000').status_code)
        self.assertEqual(404,download(record,'../output-0000').status_code)
        deadline=datetime.fromisoformat(record['recovery']['expires_at']).timestamp()
        self.assertFalse(expire_recovery(restored,sid,record,deadline-1))
        self.assertTrue(expire_recovery(restored,sid,record,deadline+1))
        self.assertIsNone(restored.peek_session(sid))
