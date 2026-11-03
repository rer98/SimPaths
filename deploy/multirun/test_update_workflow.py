"""(C) Copyright 2026, by Ross Richardson

Native update fencing, exact-container controller handover and inactive restore.
Only disposable containers and fictional files are used; no scientific model runs.
@author ross richardson
"""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch

from deploy._workflow import frontend_path
FRONT=frontend_path()
sys.path[:0]=[str(FRONT),str(FRONT/'tests/batch')]
from jasmine_web.batch import update_admission as updates
from . import application_update
from .test_backup import BackupRestoreTests,DSN
from .maintenance import GATE,service_state
import test_docker_worker as docker_fixture

VERIFIED='b'*64


@unittest.skipUnless(DSN and docker_fixture.IMAGE,'Use --docker-tests with disposable PostgreSQL')
class LiveUpdateWorkflowTests(unittest.TestCase):
    def test_live_controller_switch_adopts_the_same_container_and_only_then_resumes_queue(self):
        f=docker_fixture.DockerWorkerTests();f.setUp();self.addCleanup(f.doCleanups)
        experiment=f.submit()
        old=f.worker('before-update')
        command=old.adapter.container_command
        def held_command(lease,request):
            result=command(lease,request)
            program=request/'program.py'
            text=program.read_text()
            previous="time.sleep(settings.get('delay', 0.1))"
            self.assertIn(previous,text)
            program.write_text(text.replace(previous,
                "while not Path('allow-completion').exists():\n    time.sleep(0.05)"))
            return result
        module=f.root/'fictional_controller.py';module.write_text("VERSION='before'\n")
        candidate_body=b"VERSION='after'\n"
        candidate=hashlib.sha256(candidate_body).hexdigest()
        with patch.object(old.adapter,'container_command',side_effect=held_command),old.open():
            f.tick(old)
            lease=next(iter(old.leases.values()))
            identifier=f.executor.docker.inspect(f.executor._name(lease))['Id']
            immutable=f.fixture.sql('''SELECT id,job_id,number,execution_key,deadline,initial_resources
                FROM attempts WHERE id=%s''',(lease.attempt_id,))[0]
            reference=updates.request(f.q,candidate,mode='live')['reference']
            queued=f.submit('bob')
            f.tick(old)
            self.assertEqual(f.fixture.job('bob',queued)['attempts'],0)
        # Leaving the dispatcher context does not stop the supervisor/container.
        self.assertTrue(f.executor.docker.inspect(f.executor._name(lease))['State']['Running'])
        replacement=f.worker('after-update')
        with ExitStack() as stack:
            def install():
                self.assertEqual(updates.reporting(f.q)['current']['state'],'installing')
                staged=module.with_suffix('.next');staged.write_bytes(candidate_body);staged.replace(module)
                self.assertEqual(runpy.run_path(str(module))['VERSION'],'after')
                # Accelerate only the disposable SQL lease, never the original
                # model deadline, attempt number, inputs, seeds or time budget.
                f.fixture.expire(lease)
                stack.enter_context(replacement.open())
                def adopted():
                    f.tick(replacement)
                    return lease.attempt_id in replacement.leases
                docker_fixture.until(adopted)
            def verify():
                self.assertEqual(f.executor.docker.inspect(f.executor._name(lease))['Id'],identifier)
                self.assertEqual(f.fixture.sql('''SELECT id,job_id,number,execution_key,deadline,initial_resources
                    FROM attempts WHERE id=%s''',(lease.attempt_id,))[0],immutable)
                self.assertEqual(f.fixture.job('alice',experiment)['attempts'],1)
                self.assertEqual(f.fixture.job('bob',queued)['attempts'],0)
                self.assertTrue(updates.reporting(f.q)['admissions_paused'])
                return hashlib.sha256(module.read_bytes()+identifier.encode()).hexdigest()
            result=application_update.coordinate(f.q,reference,candidate,install=install,verify=verify,
                                                 wait_seconds=30,poll_seconds=1)
            self.assertEqual(result['state'],'complete')
            self.assertFalse(updates.reporting(f.q)['admissions_paused'])
            (f.executor.workspace(lease)/'work/allow-completion').touch()
            docker_fixture.until(lambda:f.done(replacement,'alice',experiment) and f.done(replacement,'bob',queued))
            self.assertEqual(f.fixture.job('bob',queued)['attempts'],1)
        self.assertEqual(f.executor.docker.inspect(f.executor._name(lease))['Id'],identifier)
        self.assertEqual(f.fixture.job('alice',experiment)['attempts'],1)
        self.assertEqual((f.executor.workspace(lease)/'work/starts.txt').read_text(),'started\n')
        self.assertEqual(json.loads((f.executor.workspace(lease)/'work/results.json').read_text()),
                         [dict(seed=seed,value=int(seed)*2) for seed in lease.specification['seeds']])


@unittest.skipUnless(DSN,'Use the disposable PostgreSQL/native backup runner')
class UpdateRestoreTests(unittest.TestCase):
    def roundtrip(self,online):
        from . import backup_live
        f=BackupRestoreTests();f.setUp();self.addCleanup(f.doCleanups)
        reference=updates.request(f.q,'a'*64)['reference']
        updates.mark_draining(f.q,reference,'a'*64)
        if online:
            updates.begin_install(f.q,reference,'a'*64)
            updates.fail(f.q,reference,'a'*64,reason='interrupted')
        before=updates.status(f.q)
        if online:
            f.q.backup_state=f.state
            with service_state(f.state):backup_live.create(f.q,f.state,f.backup,f.tools)
        else:f.save()
        f.restore()
        self.assertTrue((f.target/GATE).exists())
        self.assertEqual(updates.status(f.target_q),before)
        self.assertIsNone(f.target_q.claim('restored-paused'))
        self.assertTrue(updates.reporting(f.target_q)['admissions_paused'])
        if online:
            with self.assertRaises(Exception):updates.cancel(f.target_q,reference,'a'*64)
            updates.recover(f.target_q,reference,'a'*64,VERIFIED)
        else:updates.cancel(f.target_q,reference,'a'*64)
        self.assertFalse(updates.reporting(f.target_q)['admissions_paused'])
        self.assertTrue((f.target/GATE).exists(),'Update acknowledgement must not activate a restored service')
        self.assertTrue(updates.reporting(f.q)['admissions_paused'],'Restored control must not alter the source')

    def test_offline_restore_keeps_pending_update_paused_and_separate_from_activation(self):
        self.roundtrip(False)

    def test_online_restore_keeps_failed_update_paused_until_explicit_verified_recovery(self):
        self.roundtrip(True)
