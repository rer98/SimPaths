#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Disposable native XFS quota rehearsal: two small loop files, private brokers and
unprivileged fictional Docker/PostgreSQL work. Installs no service and formats
only newly created regular image files. Administrator access is needed to mount.
@author ross richardson
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from uuid import uuid4
import zipfile

ROOT=Path(__file__).resolve().parents[2]

MODEL = r'''
# (C) Copyright 2026, by Ross Richardson
# Fictional unprivileged writer for native quota and inherited-ioctl checks.
# @author ross richardson
import errno,fcntl,json,os,time
from pathlib import Path
settings=json.loads(Path('/request/settings.json').read_text())
work=Path('/work'); nested=work/'nested'; nested.mkdir()
for directory in (work,nested):
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try:
        values=bytearray(28); fcntl.ioctl(fd,0x801c581f,values,True)
        for command in (0x401c5820,0x40086602,0x40046602):
            try: fcntl.ioctl(fd,command,values)
            except OSError as error: assert error.errno==errno.EPERM,error
            else: raise AssertionError('workspace quota can be altered')
    finally: os.close(fd)
state=work/'proof-state.json'
state.write_bytes(b' '*4096)
def report(value):
    with state.open('r+b') as stream:
        stream.write(json.dumps(value).encode().ljust(4096,b' ')); stream.flush(); os.fsync(stream.fileno())
def await_gate(expected):
    deadline=time.monotonic()+90
    while Path('/request/gate').read_text().strip()!=expected:
        if time.monotonic()>deadline: raise AssertionError('fixture gate timed out')
        time.sleep(.1)
if settings['mode']=='fill':
    report(dict(guard=True,ready=True))
    await_gate('fill')
    written=0
    with (nested/'large').open('wb',buffering=0) as stream:
        try:
            while written<32*1024**2:
                written+=stream.write(b'x'*65536)
                if written%1024**2==0: os.fsync(stream.fileno())
        except OSError as error:
            assert error.errno in (errno.ENOSPC,errno.EDQUOT),error
            report(dict(error=errno.errorcode[error.errno],written=written,guard=True))
        else: raise AssertionError('filesystem did not enforce quota')
else: report(dict(guard=True,ready=True))
await_gate('go')
if settings['mode']=='fill': raise SystemExit(3)
(work/'results.json').write_text(json.dumps([dict(seed=seed,fixture='original') for seed in settings['seeds']]))
'''


def wait(function,seconds=30):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=function()
        if value:return value
        time.sleep(.1)
    raise AssertionError('Quota rehearsal phase did not finish')


def control(settings,action):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(15); connection.connect(settings['control'])
        connection.sendall((action+'\n').encode())
        result=json.loads(connection.recv(4096))
        assert result=={'ok':True},'Quota fixture control failed'


def private_json(path,value,uid,gid):
    with path.open('x') as stream:
        path.chmod(0o600); json.dump(value,stream,sort_keys=True); stream.flush(); os.fsync(stream.fileno())
    os.chown(path,uid,gid)


def filesystem_control(root):
    """Prove an exhausted project did not fill its underlying filesystem."""
    path=root/'quota-control-write'
    created=False
    try:
        with path.open('xb',buffering=0) as stream:
            created=True
            for _ in range(16): assert stream.write(b'c'*65536)==65536
            os.fsync(stream.fileno())
        info=os.statvfs(root)
        available=info.f_bavail*info.f_frsize
        assert available>=32*1024**2,'Filesystem headroom disappeared during quota failure'
        return dict(control_bytes=1024**2,available_bytes=available)
    finally:
        if created: path.unlink()


def evidence_notice(output,uid,gid):
    path=output/'COPYRIGHT.md'
    path.write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Attribution for generated fictional quota-rehearsal evidence.\n'
        '@author ross richardson\n-->\n\n'
        'Generated fixture settings and reports are attributed here. Third-party\n'
        'logs and binaries retain their existing attribution and licences.\n')
    path.chmod(0o600); os.chown(path,uid,gid)


def execute_proof(output):
    settings=json.loads(Path(os.environ['JASMINE_QUOTA_FIXTURE']).read_text())
    frontend=Path(settings['frontend'])
    sys.path[:0]=[str(ROOT),str(frontend),str(frontend/'tests')]
    report=dict(passed=False,cleanup=False,production_acceptance=False,checks=[],tests=0)
    output.mkdir(mode=0o700,parents=True)
    evidence_notice(output,os.getuid(),os.getgid())
    names=['test_workspace_quota','test_batch_docker_executor','deploy.multirun.test_vm_config',
           'deploy.multirun.test_workspace_quota','deploy.multirun.test_backup']
    suite=unittest.defaultTestLoader.loadTestsFromNames(names)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report['tests']=result.testsRun
    assert result.wasSuccessful() and not result.skipped,'Focused quota checks failed or skipped'
    print('PASS: quota ABI, launcher, recovery, storage admission and restore guards pass without skips',flush=True)
    from jasmine_web.batch.docker_executor import DockerCLI,DockerExecutor,ContainerCommand
    from jasmine_web.batch.workspace_quota import WorkspaceQuotas,WorkspaceQuotaUnavailable
    from jasmine_web.batch.policy import Policy,Resources,NotFound,fingerprint
    from jasmine_web.batch.store import Queue
    from jasmine_web.batch.worker import Worker
    from psycopg import sql
    from types import SimpleNamespace
    dsn=os.environ.get('JASMINE_BATCH_TEST_DSN')
    if not dsn: raise ValueError('Use the disposable PostgreSQL wrapper')
    q=Queue(dsn,'quota-rehearsal',schema='test_quota_'+uuid4().hex)
    source,target=Path(settings['source']),Path(settings['target'])
    docker=DockerCLI(); guard=settings['guard']; image=settings['image']
    quotas=WorkspaceQuotas(source/'execution',settings['source_socket'],guard)
    executor=DockerExecutor(source/'execution',approved_images=[image],workspace_quotas=quotas)
    leases={}; worker=None
    def passed(message):
        report['checks'].append(message); print('PASS: '+message,flush=True)
    def read_state(lease):
        path=executor.workspace(lease)/'work/proof-state.json'
        try: return json.loads(path.read_text())
        except (FileNotFoundError,json.JSONDecodeError): return None
    def ready(client):
        try: return client.ready()
        except WorkspaceQuotaUnavailable: return None
    def gate(lease,value):
        with (executor.workspace(lease)/'request/gate').open('r+b') as stream:
            stream.write(value.encode().ljust(4096,b' ')); stream.flush(); os.fsync(stream.fileno())
    def exhausted(lease):
        value=read_state(lease)
        return value if value and value.get('error') in ('ENOSPC','EDQUOT') else None
    class Adapter:
        def required_space(self,candidate): return 1
        def container_command(self,lease,request):
            (request/'model.py').write_text(MODEL)
            (request/'settings.json').write_text(json.dumps(dict(mode=lease.configuration_id,seeds=lease.specification['seeds'])))
            (request/'gate').write_bytes(b'hold'.ljust(4096,b' '))
            return ContainerCommand(image,('/usr/local/bin/python','/request/model.py'))
        def validate(self,lease,work):
            data=json.loads((work/'results.json').read_text())
            return [dict(seed=row['seed'],fingerprint=fingerprint(row)) for row in data]
    adapter=Adapter()
    stage='filesystem-write-probe'
    try:
        quotas.guard(); report['probe']=quotas.preflight()
        stage='queue-fixtures'
        q.migrate(); q.create_pool(Resources(2000,256,64),policy=Policy(attempt_seconds=120,total_seconds=360,lease_seconds=5))
        q.register_dataset('fictional-inputs','a'*64)
        for owner in ('alice','bob'):
            q.approve(owner); q.grant_dataset(owner,'fictional-inputs')
        experiments={}
        for owner,mode in (('alice','fill'),('bob','good')):
            experiments[owner]=q.submit(owner,uuid4().hex,label=mode,model_digest=image,dataset_id='fictional-inputs',
                seed_plan=['606','607'],run_sets=[dict(id=mode,parameters={})],resources=Resources(500,128,16),auto_retry=False)
        with zipfile.ZipFile(source/'retained.zip','w') as archive: archive.writestr('fictional.txt','Retained original result')
        original_zip=hashlib.sha256((source/'retained.zip').read_bytes()).hexdigest()
        worker=Worker(q,executor,adapter,'original')
        stage='protected-model-launch'
        with worker.open():
            worker.tick(); leases.update(worker.leases)
            assert len(leases)==2
            by_mode={lease.configuration_id:lease for lease in leases.values()}
            ids={key:docker.inspect(executor._name(lease))['Id'] for key,lease in by_mode.items()}
            wait(lambda:read_state(by_mode['good']))
            wait(lambda:read_state(by_mode['fill']))
            projects={key:quotas.check(lease)['project_id'] for key,lease in by_mode.items()}
            stage='quota-exhaustion-during-broker-outage'
            control(settings,'stop-source')
            with unittest.TestCase().assertRaises(WorkspaceQuotaUnavailable): quotas.ready()
            gate(by_mode['fill'],'fill')
            bad=wait(lambda:exhausted(by_mode['fill']))
            assert bad['guard'] and 8*1024**2<=bad['written']<=16*1024**2
            report['exhaustion']=dict(**bad,**filesystem_control(source))
            assert docker.inspect(ids['good'])['State']['Running']
            with q._connection() as c:
                assert c.execute('SELECT count(*) AS n FROM reservations WHERE released_at IS NULL').fetchone()['n']==2
            passed('kernel quota exhaustion and inherited ioctl protection contain a nested writer while the broker is stopped; independent writes succeed with filesystem headroom and the other owner model remains running')
            control(settings,'start-source'); wait(lambda:ready(quotas))
            for key,lease in by_mode.items():
                status=quotas.check(lease)
                assert status['limit_bytes']==16*1024**2 and status['project_id']==projects[key]
                if key=='fill':
                    assert max(8*1024**2,bad['written']-1024**2)<=status['used_bytes']<=16*1024**2
                    report['exhaustion']['quota_used_bytes']=status['used_bytes']
            with q._connection() as c:
                c.execute("UPDATE attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE phase<>'finished'")
        recovered=DockerExecutor(executor.root,approved_images=[image],workspace_quotas=quotas)
        stage='original-container-adoption'
        worker=Worker(q,recovered,adapter,'replacement')
        with worker.open():
            worker.tick(claim_new=False)
            for key,lease in by_mode.items():
                assert docker.inspect(recovered._name(lease))['Id']==ids[key]
                gate(lease,'go')
            passed('fresh broker and dispatcher processes reuse the same projects, frozen byte limits, original containers and attempts')
            def finished():
                worker.tick(claim_new=False)
                return all(q.inspect(owner,experiment)['jobs'][0]['state'] in ('succeeded','failed','review') for owner,experiment in experiments.items())
            wait(finished)
            for lease in leases.values(): recovered.cleanup(lease)
        assert q.inspect('alice',experiments['alice'])['jobs'][0]['state']!='succeeded'
        stage='settled-results-and-capacity'
        assert q.inspect('bob',experiments['bob'])['jobs'][0]['state']=='succeeded'
        with unittest.TestCase().assertRaises(NotFound): q.inspect('alice',experiments['bob'])
        assert quotas.ready()['remaining_reserved_bytes']==0
        assert hashlib.sha256((source/'retained.zip').read_bytes()).hexdigest()==original_zip
        with zipfile.ZipFile(source/'retained.zip') as archive: assert archive.testzip() is None
        with q._connection() as c:
            assert c.execute('SELECT count(*) AS n FROM attempts').fetchone()['n']==2
            assert [row['actual_seed'] for row in c.execute('SELECT actual_seed FROM repetitions WHERE actual_seed IS NOT NULL ORDER BY ordinal')]==['606','607']
            assert c.execute('SELECT count(*) AS n FROM reservations WHERE released_at IS NULL').fetchone()['n']==0
        passed('oversized work cannot publish success; the other owner completes both original seeds once, retained ZIP bytes survive, and only confirmed stopped capacity is released')
        for name in ('work','request'):
            shutil.rmtree(executor.workspace(by_mode['fill'])/name)
        assert hashlib.sha256((source/'retained.zip').read_bytes()).hexdigest()==original_zip
        good=by_mode['good']; saved=source/'execution'/good.execution_key/'work/results.json'
        saved_hash=hashlib.sha256(saved.read_bytes()).hexdigest()
        # Filesystem metadata is reconstructed on an independent destination;
        # generic backup/activation regression cases separately exercise the gate.
        for entry in source.iterdir():
            destination=target/entry.name
            if entry.is_dir(): shutil.copytree(entry,destination,dirs_exist_ok=True)
            else: shutil.copyfile(entry,destination)
        target_quotas=WorkspaceQuotas(target/'execution',settings['target_socket'],guard)
        stage='independent-filesystem-restore'
        from deploy.multirun.backup import restore_workspace_quotas
        with q._connection() as c: restore_workspace_quotas(c,q,target,target_quotas)
        assert target_quotas.check(good)['limit_bytes']==16*1024**2
        assert target_quotas.check(good)['project_id']!=quotas.check(good)['project_id']
        assert hashlib.sha256((target/'execution'/good.execution_key/'work/results.json').read_bytes()).hexdigest()==saved_hash
        assert target_quotas.ready()['remaining_reserved_bytes']==0
        control(settings,'restart-target'); wait(lambda:ready(target_quotas))
        assert target_quotas.check(good)['limit_bytes']==16*1024**2
        passed('independent filesystem recovery rebuilds destination project IDs from verified per-attempt allowances; saved bytes and limits survive another broker restart')
        # Writing into a restored settled workspace still encounters the old hard
        # limit. This is a trusted probe, not a new attempt or scientific rerun.
        fd=os.open(target/'execution'/good.execution_key/'work/restored-fill',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        written=0
        try:
            try:
                for _ in range(512):
                    written+=os.write(fd,b'y'*65536); os.fsync(fd)
            except OSError as error:
                import errno
                assert error.errno in (errno.ENOSPC,errno.EDQUOT),error
                report['restored_exhaustion']=dict(error=errno.errorcode[error.errno],written_bytes=written)
            else: raise AssertionError('restored hard quota did not hold')
        finally: os.close(fd)
        assert 8*1024**2<=written<=16*1024**2
        status=target_quotas.check(good)
        assert status['limit_bytes']==16*1024**2 and max(8*1024**2,written-1024**2)<=status['used_bytes']<=16*1024**2
        report['restored_exhaustion'].update(quota_used_bytes=status['used_bytes'],**filesystem_control(target))
        assert hashlib.sha256((target/'execution'/good.execution_key/'work/results.json').read_bytes()).hexdigest()==saved_hash
        passed('restored settled output retains its hard limit; further writes cannot consume unrestricted space or change the saved result')
        report['passed']=True
    except Exception as error:
        report['failure']=dict(stage=stage,error_type=type(error).__name__)
        raise
    finally:
        clean=True
        # Only proof mounts/identities: never remove another local model/container.
        for identifier in docker.call('container','ls','-aq','--filter','label=jasmine.batch=true').splitlines():
            row=docker.inspect(identifier)
            if row and any(m.get('Type')=='bind' and Path(m['Source']).is_relative_to(source/'execution') for m in row['Mounts']):
                docker.call('container','rm','--force',row['Id'])
                clean=clean and docker.inspect(row['Id']) is None
        with q._connection() as c: c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(q.schema)))
        report['cleanup']=clean
        (output/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    if not report['passed'] or not report['cleanup']: raise AssertionError('Quota proof did not pass/clean up')
    print('PASSED: '+str(output/'report.json'),flush=True)


def rehearsal(args):
    if os.geteuid()!=0 or not os.environ.get('SUDO_UID') or int(os.environ['SUDO_UID'])==0:
        raise ValueError('Run with sudo from the normal laptop account; the proof itself runs unprivileged')
    uid,gid=int(os.environ['SUDO_UID']),int(os.environ['SUDO_GID'])
    account=pwd.getpwuid(uid)
    frontend=args.frontend.resolve(strict=True); python=args.python.absolute()
    if not python.is_file(): raise ValueError('Use the existing test Python interpreter')
    output=args.output.absolute()
    if output.exists() or any(p.is_symlink() for p in (output,*output.parents)):
        raise ValueError('Choose a new evidence directory without symlink ancestors')
    if not output.parent.is_dir() or output.parent.stat().st_uid!=uid:
        raise ValueError('Evidence parent must already exist and belong to the ordinary account')
    for tool in ('mkfs.xfs','mount','umount','cc','docker'):
        if not shutil.which(tool): raise ValueError('Install '+tool+' before the rehearsal')
    temporary_root=args.temporary_root.resolve(strict=True)
    if shutil.disk_usage(temporary_root).free<2*1024**3: raise ValueError('Need 2 GiB free on the temporary-files filesystem')
    output.mkdir(mode=0o700,parents=True); os.chown(output,uid,gid)
    evidence_notice(output,uid,gid)
    report=dict(passed=False,cleanup=False,production_acceptance=False,loop_images=2,loop_image_mib=512)
    tag=uuid4().hex
    runtime=Path('/var/lib')/('jasmine-quota-rehearsal-'+tag)
    sockets=Path('/run')/('jasmine-quota-'+tag)
    base=Path(tempfile.mkdtemp(prefix='simpaths-quota-',dir=temporary_root))
    mounted=[]; processes={}; logs=[]; lock=threading.RLock(); stopping=threading.Event(); control_thread=None
    env=dict(os.environ,JASMINE_WEB_REPO=str(frontend),TMPDIR=str(temporary_root),PIP_DEFAULT_TIMEOUT='60',
        DOCKER_CONFIG=str(Path(account.pw_dir)/'.docker'))
    groups=os.getgrouplist(account.pw_name,gid)
    def user_run(command,**options):
        return subprocess.run(command,user=uid,group=gid,extra_groups=groups,umask=0o077,env=env,**options)
    configs={}
    def start(role):
        log=(output/('broker-'+role+'.log')).open('ab'); os.chown(log.name,uid,gid); logs.append(log)
        Path(log.name).chmod(0o600)
        processes[role]=subprocess.Popen(['/usr/bin/python3','-I',str(runtime/'broker.py'),str(configs[role])],
            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,env={'PATH':'/usr/bin:/usr/sbin:/bin:/sbin','LC_ALL':'C'})
    def stop(role):
        process=processes.pop(role,None)
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    try:
        base.chmod(0o711); runtime.mkdir(mode=0o711); runtime.chmod(0o711)
        sockets.mkdir(mode=0o711); sockets.chmod(0o711)
        image=user_run(['docker','image','inspect','python:3.12-slim','--format','{{.Id}}'],check=True,capture_output=True,text=True).stdout.strip()
        shutil.copyfile(frontend/'deploy/workspace_quota_broker.py',runtime/'broker.py'); (runtime/'broker.py').chmod(0o500)
        subprocess.run(['cc','-static','-O2','-Wall','-Wextra','-Werror',str(frontend/'deploy/workspace_guard.c'),'-o',str(runtime/'guard')],
            check=True,capture_output=True,timeout=60)
        (runtime/'guard').chmod(0o555)
        settings=dict(frontend=str(frontend),guard=str(runtime/'guard'),image=image,control=str(sockets/'control.sock'))
        for index,role in enumerate(('source','target')):
            image_file=base/(role+'.img')
            with image_file.open('xb') as stream: stream.truncate(512*1024**2)
            mountpoint=base/role; mountpoint.mkdir(mode=0o700)
            with (output/(role+'-filesystem.log')).open('wb') as log:
                os.chown(log.name,uid,gid); Path(log.name).chmod(0o600)
                subprocess.run(['mkfs.xfs','-q',str(image_file)],check=True,stdout=log,stderr=subprocess.STDOUT,timeout=30)
                subprocess.run(['mount','-t','xfs','-o','loop,prjquota,nodev,nosuid,noexec',str(image_file),str(mountpoint)],
                    check=True,stdout=log,stderr=subprocess.STDOUT,timeout=30)
            mounted.append(mountpoint); mountpoint.chmod(0o711)
            state=mountpoint/'state'; state.mkdir(mode=0o700); os.chown(state,uid,gid)
            for name in ('execution','artifacts'):
                path=state/name; path.mkdir(mode=0o700); os.chown(path,uid,gid)
            ledger=runtime/(role+'-ledger'); ledger.mkdir(mode=0o700)
            socket_dir=sockets/role; socket_dir.mkdir(mode=0o755); socket_dir.chmod(0o755)
            configs[role]=runtime/(role+'.json')
            config=dict(execution_root=str(state/'execution'),artifact_root=str(state/'artifacts'),
                ledger_root=str(ledger),socket_path=str(socket_dir/'broker.sock'),service_uid=uid,service_gid=gid,
                project_first=1000000+index*1000000,project_last=1000999+index*1000000,
                max_bytes=128*1024**2,inode_limit=100000,reserve_bytes=32*1024**2)
            private_json(configs[role],config,0,0)
            settings[role]=str(state); settings[role+'_socket']=config['socket_path']
            start(role)
            wait(lambda:Path(config['socket_path']).exists() or processes[role].poll() is not None)
            if processes[role].poll() is not None: raise RuntimeError('Private quota broker did not start; inspect its log')
        settings_file=output/'fixture.json'; private_json(settings_file,settings,uid,gid)
        env['JASMINE_QUOTA_FIXTURE']=str(settings_file)
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); server.bind(settings['control'])
        os.chown(settings['control'],uid,gid); Path(settings['control']).chmod(0o600); server.listen(4); server.settimeout(.5)
        def controls():
            while not stopping.is_set():
                try: connection,_=server.accept()
                except socket.timeout: continue
                except OSError:
                    if stopping.is_set(): return
                    raise
                with connection:
                    connection.settimeout(5)
                    try:
                        _,peer,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                        action=connection.recv(128).decode().strip()
                        if peer!=uid or action not in ('stop-source','start-source','restart-target'): raise ValueError()
                        with lock:
                            if action=='stop-source':stop('source')
                            elif action=='start-source':start('source')
                            else:stop('target');start('target')
                        connection.sendall(b'{"ok":true}')
                    except Exception:
                        try:connection.sendall(b'{"ok":false}')
                        except OSError:pass
        control_thread=threading.Thread(target=controls,daemon=True); control_thread.start()
        print('Running fictional models unprivileged on two disposable 512 MiB XFS loop files',flush=True)
        command=[str(python),str(frontend/'scripts/test_batch_queue.py'),'--proof-only','--proof-script',str(Path(__file__).resolve()),
                 '--proof-requirements',str(ROOT/'deploy/multirun/requirements.txt'),'--output',str(output/'postgres-proof')]
        result=user_run(command,cwd=frontend)
        report['passed']=result.returncode==0
    except (Exception,KeyboardInterrupt) as error:
        report['error_type']=type(error).__name__
        if isinstance(error,subprocess.CalledProcessError) and error.stderr:
            details=output/'failure.log'
            details.write_bytes(error.stderr.encode() if isinstance(error.stderr,str) else error.stderr)
            details.chmod(0o600); os.chown(details,uid,gid)
        print('STOPPED: '+type(error).__name__+' during the quota rehearsal; inspect '+str(output),flush=True)
    finally:
        stopping.set()
        if control_thread is not None:
            server.close(); control_thread.join(timeout=2)
        with lock:
            for role in list(processes):stop(role)
        for log in logs:log.close()
        clean=True
        for mountpoint in reversed(mounted):
            try:
                result=subprocess.run(['umount',str(mountpoint)],capture_output=True,timeout=30)
                clean=clean and result.returncode==0
            except (OSError,subprocess.TimeoutExpired):clean=False
        if clean:
            for path in (base,runtime,sockets):
                if path.exists():shutil.rmtree(path)
        report['cleanup']=clean
        if not clean:
            report['passed']=False; report['retained_temporary_root']=str(base)
            print('Temporary filesystem is busy; retained only this rehearsal directory: '+str(base),flush=True)
        private_json(output/'report.json',report,uid,gid)
    print(('PASSED: ' if report['passed'] else 'FAILED: ')+str(output/'report.json'),flush=True)
    return 0 if report['passed'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-proof',action='store_true')
    parser.add_argument('--frontend',type=Path)
    parser.add_argument('--python',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--temporary-root',type=Path,default=Path('/tmp/codex-rer'))
    args=parser.parse_args()
    if args.execute_proof:
        if os.geteuid()==0: parser.error('The database/model proof must run as the ordinary user')
        execute_proof(args.output);return 0
    if not args.frontend or not args.python:parser.error('--frontend and --python are required for the administrator fixture')
    return rehearsal(args)


if __name__=='__main__':raise SystemExit(main())
