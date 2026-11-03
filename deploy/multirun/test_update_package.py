"""(C) Copyright 2026, by Ross Richardson

Qualify trusted application packages and fixed reversible controller replacement.
Uses private fictional files and an in-memory host; never runs system commands.
@author ross richardson
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import update_package as package


class Connection:
    def __init__(self, queue):self.queue=queue;self.value=None

    def execute(self, sql, args=()):
        if sql.startswith('SET TRANSACTION'):self.value=[]
        elif 'FROM schema_version' in sql:self.value=self.queue.schema
        elif 'SELECT a.id' in sql:self.value=self.queue.attempts
        elif 'SELECT cpu_millis' in sql:self.value=self.queue.pool
        elif 'SELECT execution_key' in sql:
            self.value=[dict(execution_key=key) for key,phase in self.queue.phases.items() if phase!='finished']
        elif 'SELECT phase' in sql:
            self.value=dict(phase=self.queue.phases[args[1]]) if args[1] in self.queue.phases else None
        else:raise AssertionError('Unexpected metadata query: '+sql)
        return self

    def fetchall(self):return deepcopy(self.value)
    def fetchone(self):return deepcopy(self.value)


class Queue:
    pool_id='fictional'

    def __init__(self):
        self.schema=[dict(version=1,checksum='a'*64)]
        self.pool=dict(cpu_millis=8000,memory_mib=16000,storage_mib=32000,
                       hold_cpu=2000,hold_memory=4000,hold_storage=1000,
                       processing_cpu=1000,processing_memory=2000,growth_memory=1024,policy={})
        self.attempts=[dict(id='attempt-1',job_id='job-1',number=1,execution_key='batch-fictional',
                            deadline='2026-11-04T00:00:00Z',resources=dict(cpu_millis=1000,memory_mib=3000),
                            started_at='2026-11-03T16:00:00Z',configuration_id='configuration-1',
                            model_digest='b'*64,prepared_fingerprint='c'*64)]
        self.phases={'batch-fictional':'running'}

    def migrations(self):return [(row['version'],row['checksum'],'fictional.sql') for row in self.schema]

    @contextmanager
    def _connection(self):yield Connection(self)


class Host:
    def __init__(self):
        self.calls=[]
        self.states={package.PRIVATE:'active',package.PUBLIC:'active','simpaths-backup.service':'inactive'}
        self.identities={unit:'MainPID=123\nInvocationID='+unit+'\nActiveState=active\n' for unit in package.SINGLE}
        self.containers={'jasmine-batch-fictional':'unchanged-container-id'}
        self.preflight_failures=0

    def command(self,*args,**kwargs):
        args=tuple(map(str,args));self.calls.append(args)
        if args[:2]==('systemctl','show'):
            if '--property=MainPID' in args:return self.identities[args[2]]
            return self.states[args[2]]+'\n'
        if args[:2] in (('systemctl','stop'),('systemctl','start')):
            if args[2] not in (package.PRIVATE,package.PUBLIC):raise AssertionError('An unrelated unit was changed')
            self.states[args[2]]='inactive' if args[1]=='stop' else 'active';return ''
        if args[0]=='runuser':
            if self.preflight_failures:
                self.preflight_failures-=1;raise RuntimeError('Fictional startup not ready')
            return ''
        if args[0]=='curl':
            return json.dumps(dict(ready=True) if ':443/' in args[-1] else dict(status='ok',python='ok'))
        if args[:2]==('docker','inspect'):return self.containers[args[-1]]+'\n'
        raise AssertionError('An unexpected host command was attempted: '+str(args))


class PackageTrustTests(unittest.TestCase):
    def info(self,**changes):
        return SimpleNamespace(**(dict(st_mode=stat.S_IFREG|0o644,st_uid=0,st_nlink=1,st_size=10)|changes))

    def trusted(self,*,bad_parent=None,leaf=None,directory=False):
        location=Path('/trusted/update/source.py')
        default_parent=self.info(st_mode=stat.S_IFDIR|0o755)
        leaf=leaf or self.info()
        def lstat(path):
            if path==location:return leaf
            if path==location.parent and bad_parent is not None:return bad_parent
            return default_parent
        with patch.object(Path,'lstat',lstat),patch.object(Path,'stat',lambda path:leaf):
            return package.trusted(location,directory=directory)

    def test_root_owned_regular_files_and_directories_are_accepted(self):
        self.assertEqual(self.trusted(),Path('/trusted/update/source.py'))
        self.trusted(leaf=self.info(st_mode=stat.S_IFDIR|0o700),directory=True)

    def test_relative_paths_ownership_writable_ancestors_and_symlinks_are_rejected(self):
        with self.assertRaises(ValueError):package.trusted('relative.py')
        for info in (self.info(st_uid=1000),self.info(st_mode=stat.S_IFDIR|0o775),
                     self.info(st_mode=stat.S_IFLNK|0o777)):
            with self.subTest(info=info),self.assertRaises(ValueError):self.trusted(bad_parent=info)
        with self.assertRaises(ValueError):self.trusted(leaf=self.info(st_mode=stat.S_IFLNK|0o777))

    def test_hardlinks_special_files_and_unbounded_sources_are_rejected(self):
        for info in (self.info(st_nlink=2),self.info(st_size=package.MAX_FILE+1),
                     self.info(st_mode=stat.S_IFIFO|0o600)):
            with self.subTest(info=info),self.assertRaises(ValueError):self.trusted(leaf=info)

    def test_only_canonical_application_source_paths_are_accepted(self):
        package.relative('JAS-mine-web/jasmine_web/batch/notifications.py')
        package.relative('SimPaths/deploy/multirun/runtime.py')
        for value in ('/etc/passwd','JAS-mine-web/../etc/passwd',
                      'JAS-mine-web/jasmine_web/batch/./notifications.py',
                      'JAS-mine-web//jasmine_web/batch/notifications.py',
                      'JAS-mine-web/jasmine_web/batch/038_migration.sql',
                      'SimPaths/input/DatabaseCountryYear.xlsx','Other/deploy/multirun/a.py',
                      'SimPaths/deploy/secrets.py'):
            with self.subTest(path=value),self.assertRaises(ValueError):package.relative(value)


class ApplicationPackageTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.base=Path(temporary.name);self.root=self.base/'installed';self.root.mkdir()
        self.config=self.base/'config';self.config.mkdir();(self.config/'settings.toml').write_text('fictional private setting')
        self.source=self.base/'package';self.source.mkdir()
        self.state=self.base/'state';self.state.mkdir()
        lock=self.state/'.backup-files.lock';lock.touch(mode=0o600)
        self.q=Queue();self.host=Host();self.names=[
            'JAS-mine-web/jasmine_web/batch/notifications.py',
            'JAS-mine-web/jasmine_web/batch/browser_static/batch_admin.js']
        self.before={name:('previous '+name).encode() for name in self.names}
        self.after={name:('candidate '+name).encode() for name in self.names}
        for name in self.names:
            current=self.root/name;current.parent.mkdir(parents=True,exist_ok=True);current.write_bytes(self.before[name])
            staged=self.source/'programs'/name;staged.parent.mkdir(parents=True,exist_ok=True);staged.write_bytes(self.after[name])
        # Ownership/link policy is separately exercised above. This fixture's
        # private /tmp files never pretend to be deployable root-owned packages.
        for target,value in (('ROOT',self.root),('CONFIG',self.config)):
            patcher=patch.object(package,target,value);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(package,'trusted',side_effect=lambda path,**kwargs:Path(path));patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(package,'command',side_effect=self.host.command);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(package.time,'sleep',return_value=None);patcher.start();self.addCleanup(patcher.stop)
        self.plan=self.write_plan()

    def write_plan(self,*,mode='live',evidence_changes=None,plan_changes=None):
        targets={name:dict(before=package.digest(self.before[name]),after=package.digest(self.after[name])) for name in self.names}
        evidence=dict(passed=True,cleanup=True,schema_unchanged=True,targets={name:row['after'] for name,row in targets.items()},
                      same_container_verified=True,frozen_attempts_verified=True,controller_switch_verified=True,
                      tests=3,errors=0,failures=0,skipped=0,sources_unchanged=True)
        evidence.update(evidence_changes or {})
        body=json.dumps(evidence).encode();(self.source/'qualification.json').write_bytes(body)
        plan=dict(format=package.FORMAT,mode=mode,schema=deepcopy(self.q.schema),targets=targets,
                  qualification=dict(sha256=package.digest(body)))
        plan.update(plan_changes or {})
        (self.source/'plan.json').write_text(json.dumps(plan))
        return plan

    def validate(self):
        return package.validate_package(self.source,package.digest((self.source/'plan.json').read_bytes()),self.q)

    def installation(self):
        result=package.Installation(SimpleNamespace(state=self.state),self.q,self.source,self.plan)
        self.addCleanup(result.release_fence)
        # Native file-lock behavior is checked separately from this fixed-host
        # state-machine fixture; no service account lookup is needed here.
        result.backup_fence=lambda:None
        return result

    def test_exact_same_schema_and_candidate_receipt_are_required(self):
        self.assertEqual(self.validate(),self.plan)
        with self.assertRaises(ValueError):package.validate_package(self.source,'f'*64,self.q)
        (self.source/'qualification.json').write_text('{}')
        with self.assertRaises(ValueError):self.validate()

    def test_schema_changes_extra_commands_and_unqualified_live_targets_are_rejected(self):
        for changes in (dict(schema=[]),dict(command=['sh','-c','unsafe']),dict(mode='unknown')):
            self.write_plan(plan_changes=changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.validate()
        for flag in ('same_container_verified','frozen_attempts_verified','controller_switch_verified'):
            self.write_plan(evidence_changes={flag:False})
            with self.subTest(flag=flag),self.assertRaises(ValueError):self.validate()

    def test_installed_and_candidate_bytes_must_match_reviewed_hashes(self):
        target=self.names[0]
        (self.root/target).write_bytes(b'changed installed program')
        with self.assertRaises(ValueError):self.validate()
        (self.root/target).write_bytes(self.before[target])
        (self.source/'programs'/target).write_bytes(b'changed staged program')
        with self.assertRaises(ValueError):self.validate()

    def test_backend_runtime_changes_require_drain_mode(self):
        name='SimPaths/deploy/multirun/runtime.py';self.names.append(name)
        self.before[name]=b'old runtime';self.after[name]=b'new runtime'
        for directory,body in ((self.root,self.before[name]),(self.source/'programs',self.after[name])):
            path=directory/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
        self.write_plan()
        with self.assertRaises(ValueError):self.validate()
        self.plan=self.write_plan(mode='drain',evidence_changes=dict(same_container_verified=False))
        self.validate()

    def test_fixed_live_install_preserves_container_attempt_pool_settings_and_singlerun(self):
        installation=self.installation();before=deepcopy(self.q.attempts),deepcopy(self.q.pool)
        with patch.object(package.os,'chown',return_value=None):installation.install()
        receipt=installation.verify()
        self.assertEqual(len(receipt),64)
        self.assertEqual((self.q.attempts,self.q.pool),before)
        for name in self.names:self.assertEqual((self.root/name).read_bytes(),self.after[name])
        self.assertFalse(any(call[:2]==('systemctl','stop') and call[2] in package.SINGLE for call in self.host.calls))
        self.assertTrue(any(call[:2]==('docker','inspect') for call in self.host.calls))
        self.assertTrue(all(call[:2]!=('docker','run') for call in self.host.calls))

    def test_partial_publication_failure_rolls_back_all_touched_programs(self):
        installation=self.installation();original=package.atomic
        def second_fails(path,body):
            if path==self.root/self.names[1] and body==self.after[self.names[1]]:
                raise OSError('Fictional atomic-write failure')
            return original(path,body)
        with patch.object(package.os,'chown',return_value=None),patch.object(package,'atomic',side_effect=second_fails):
            with self.assertRaises(OSError):installation.install()
            receipt=installation.rollback()
        self.assertEqual(len(receipt),64)
        for name in self.names:self.assertEqual((self.root/name).read_bytes(),self.before[name])
        self.assertTrue(all(self.host.states[unit]=='active' for unit in (package.PRIVATE,package.PUBLIC)))

    def test_interrupted_publication_recovers_from_the_persisted_receipt_in_a_new_process(self):
        installation=self.installation();original=package.atomic
        def second_fails(path,body):
            if path==self.root/self.names[1] and body==self.after[self.names[1]]:
                raise OSError('Fictional controller crash between publications')
            return original(path,body)
        with patch.object(package.os,'chown',return_value=None),patch.object(package,'atomic',side_effect=second_fails):
            with self.assertRaises(OSError):installation.install()
        self.assertTrue((self.source/'install-progress.json').is_file())
        with self.assertRaises(ValueError):self.validate()
        candidate=package.digest((self.source/'plan.json').read_bytes())
        plan=package.validate_package(self.source,candidate,self.q,allow_recovery=True)
        with patch.object(package.os,'geteuid',return_value=0),patch.object(package.os,'chown',return_value=None):
            callbacks=package.recovery_callbacks(SimpleNamespace(state=self.state),self.q,self.source,plan)
            receipt=callbacks['rollback']()
        self.assertEqual(len(receipt),64)
        for name in self.names:self.assertEqual((self.root/name).read_bytes(),self.before[name])
        self.assertTrue(all(self.host.states[unit]=='active' for unit in (package.PRIVATE,package.PUBLIC)))

    def test_recovery_rejects_changed_preimages_and_unrecorded_partial_publication(self):
        target=self.names[0]
        (self.root/target).write_bytes(self.after[target])
        with patch.object(package.os,'geteuid',return_value=0),self.assertRaises(ValueError):
            package.recovery_callbacks(SimpleNamespace(state=self.state),self.q,self.source,self.plan)
        (self.root/target).write_bytes(self.before[target])
        installation=self.installation()
        with patch.object(package.os,'chown',return_value=None):installation.install()
        (self.source/'rollback'/target).write_bytes(b'changed rollback bytes')
        with patch.object(package.os,'geteuid',return_value=0),self.assertRaises(ValueError):
            package.recovery_callbacks(SimpleNamespace(state=self.state),self.q,self.source,self.plan)

    def test_attempt_deadline_changed_container_or_singlerun_identity_fails_verification(self):
        installation=self.installation()
        with patch.object(package.os,'chown',return_value=None):installation.install()
        old=self.q.attempts[0]['deadline'];self.q.attempts[0]['deadline']='altered deadline'
        with self.assertRaises(ValueError):installation.verify()
        self.q.attempts[0]['deadline']=old
        self.host.containers['jasmine-batch-fictional']='replacement-container'
        with self.assertRaises(ValueError):installation.verify()
        self.host.containers['jasmine-batch-fictional']='unchanged-container-id'
        self.host.identities[package.SINGLE[0]]='different invocation'
        with self.assertRaises(ValueError):installation.verify()

    def test_completed_running_work_can_finish_without_keeping_its_old_container(self):
        installation=self.installation()
        with patch.object(package.os,'chown',return_value=None):installation.install()
        self.q.phases['batch-fictional']='finished';self.host.containers.clear()
        self.assertEqual(len(installation.verify()),64)

    def test_busy_backup_or_stopped_service_refuses_without_starting_or_stopping_units(self):
        for unit in ('simpaths-backup.service',package.PRIVATE):
            self.host.states['simpaths-backup.service']='inactive';self.host.states[package.PRIVATE]='active'
            self.host.states[unit]='active' if unit=='simpaths-backup.service' else 'inactive'
            self.host.calls.clear();installation=self.installation()
            with self.subTest(unit=unit),self.assertRaises(ValueError):installation.install()
            if unit==package.PRIVATE:
                with self.assertRaises(ValueError):installation.rollback()
            else:installation.rollback()
            self.assertFalse(any(call[:2] in (('systemctl','start'),('systemctl','stop')) for call in self.host.calls))

    def test_preflight_retries_are_bounded_and_never_restart_simulations(self):
        installation=self.installation();self.host.preflight_failures=1
        installation.service_ready()
        self.assertEqual(sum(call[0]=='runuser' for call in self.host.calls),2)
        self.assertFalse(any(call[0]=='docker' for call in self.host.calls))
        self.host.preflight_failures=5
        with patch.object(package.time,'monotonic',side_effect=[0,121]),self.assertRaises(RuntimeError):
            installation.service_ready()

    def test_backup_fence_excludes_capture_but_keeps_shared_service_readers_available(self):
        installation=self.installation()
        with patch.object(package.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid())):
            package.Installation.backup_fence(installation)
        peer=os.open(self.state/'.backup-files.lock',os.O_RDONLY)
        self.addCleanup(os.close,peer)
        fcntl.flock(peer,fcntl.LOCK_SH|fcntl.LOCK_NB)
        fcntl.flock(peer,fcntl.LOCK_UN)
        with self.assertRaises(BlockingIOError):fcntl.flock(peer,fcntl.LOCK_EX|fcntl.LOCK_NB)
        installation.release_fence()
        fcntl.flock(peer,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_unowned_public_or_linked_backup_fences_are_rejected(self):
        installation=self.installation();lock=self.state/'.backup-files.lock'
        with patch.object(package.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid()+1)):
            with self.assertRaises(ValueError):package.Installation.backup_fence(installation)
        lock.chmod(0o644)
        with patch.object(package.pwd,'getpwnam',return_value=SimpleNamespace(pw_uid=os.getuid())):
            with self.assertRaises(ValueError):package.Installation.backup_fence(installation)
        lock.unlink();target=self.state/'other.lock';target.touch(mode=0o600);lock.symlink_to(target)
        with self.assertRaises(ValueError):package.Installation.backup_fence(installation)

    def test_only_root_may_obtain_installer_callbacks(self):
        with patch.object(package.os,'geteuid',return_value=1000),self.assertRaises(ValueError):
            package.build_callbacks(SimpleNamespace(),self.q,self.source,self.plan)
