"""(C) Copyright 2026, by Ross Richardson

Fixed, reversible installer for qualified same-schema MultiRun application packages.
Packages contain data and source bytes, never operator-supplied shell commands.
@author ross richardson
"""
import hashlib
import fcntl
import json
import os
import pwd
from pathlib import Path
import stat
import subprocess
import tempfile
import time

FORMAT = 'simpaths.application-update.v1'
ROOT = Path('/opt/simpaths-online')
CONFIG = Path('/etc/simpaths-online')
PRIVATE = 'simpaths-executor.service'
PUBLIC = 'simpaths-multirun.service'
SINGLE = ('simpaths-singlerun-executor.service', 'simpaths-singlerun.service')
LIVE_FILES = {'JAS-mine-web/jasmine_web/batch/notifications.py',
              'JAS-mine-web/jasmine_web/batch/administration.py'}
PROJECTS = {'JAS-mine-web', 'SimPaths'}
MAX_FILE = 32 * 1024**2


def digest(body):
    return hashlib.sha256(body).hexdigest()


def trusted(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute():raise ValueError('Use an absolute trusted update path')
    for parent in (path, *path.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Use root-owned update files outside writable directories')
    info = path.stat()
    if directory:
        if not stat.S_ISDIR(info.st_mode): raise ValueError('Require an update directory')
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
        raise ValueError('Require a bounded regular update file')
    return path


def settings_snapshot(options):
    """Hash stable configuration bytes while preserving credential ownership.

    Configuration directories and ordinary settings remain root controlled.
    The configured private PostgreSQL credential and the installed scheduled
    backup settings retain their existing service owner. These specific data
    roles do not relax trust checks for executable application code.
    """
    root=trusted(CONFIG,directory=True)
    credential=getattr(options,'dsn_file',None)
    credential=Path(credential) if credential is not None else None
    service_files={root/'backup.toml':128*1024}
    if credential is not None:service_files[credential]=8192
    service_uid=None
    pending=[root];result={};entries=total=0
    identity=lambda value:tuple(getattr(value,key) for key in (
        'st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns'))
    while pending:
        directory=pending.pop()
        trusted(directory,directory=True)
        for path in sorted(directory.iterdir()):
            entries+=1
            if entries>1024:raise ValueError('Private configuration inventory exceeds its bound')
            observed=path.lstat()
            private_settings=path in service_files
            if stat.S_ISDIR(observed.st_mode) and not private_settings:
                trusted(path,directory=True);pending.append(path);continue
            if not stat.S_ISREG(observed.st_mode):
                raise ValueError('Private configuration contains a link or special entry')
            if private_settings:
                if service_uid is None:
                    service_uid=pwd.getpwnam('simpaths-online').pw_uid
                    if service_uid==0:raise ValueError('Require a distinct private service account')
            else:trusted(path)
            limit=service_files.get(path,MAX_FILE)
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as source:
                before=os.fstat(source.fileno())
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>limit
                        or identity(before)!=identity(observed)
                        or private_settings and (before.st_uid!=service_uid or before.st_mode&0o077)
                        or not private_settings and (before.st_uid!=0 or before.st_mode&0o022)):
                    raise ValueError('Invalid private configuration file')
                total+=before.st_size
                if total>64*1024**2:raise ValueError('Private configuration bytes exceed their bound')
                digestor=hashlib.sha256();amount=0
                while block:=source.read(65536):
                    amount+=len(block)
                    if amount>limit:raise ValueError('Private configuration grew beyond its bound')
                    digestor.update(block)
                if (identity(before)!=identity(os.fstat(source.fileno()))
                        or identity(before)!=identity(path.lstat())):
                    raise ValueError('Private configuration changed while being read')
            result[str(path.relative_to(root))]=digestor.hexdigest()
    return result


def relative(name):
    if not isinstance(name, str): raise ValueError('Require an application source path')
    path = Path(name)
    if (str(path)!=name or path.is_absolute() or '..' in path.parts or len(path.parts)<3 or path.parts[0] not in PROJECTS
            or path.suffix not in ('.py', '.js', '.css', '.html')):
        raise ValueError('Only application source files may be replaced')
    prefix = 'jasmine_web/batch/' if path.parts[0]=='JAS-mine-web' else 'deploy/multirun/'
    if not str(Path(*path.parts[1:])).startswith(prefix):
        raise ValueError('The update path is outside the application')
    return path


def live_path(name):
    return name in LIVE_FILES or (name.startswith('JAS-mine-web/jasmine_web/batch/browser_static/')
                                  and Path(name).suffix in ('.js', '.css', '.html'))


def validate_package(source, candidate, queue, *, allow_recovery=False):
    source = trusted(source, directory=True)
    body = trusted(source/'plan.json').read_bytes()
    if len(body)>1024**2 or digest(body)!=candidate:
        raise ValueError('The approved application-update plan differs')
    plan = json.loads(body)
    if (set(plan)!={'format','mode','schema','targets','qualification'} or plan['format']!=FORMAT
            or plan['mode'] not in ('live','drain') or not isinstance(plan['targets'],dict)
            or not 1<=len(plan['targets'])<=128):
        raise ValueError('Require a bounded, qualified application update')
    expected = [dict(version=v,checksum=h) for v,h,_ in queue.migrations()]
    if plan['schema']!=expected:
        raise ValueError('Schema-changing releases require a separate reviewed maintenance procedure')
    with queue._connection() as c:
        c.execute('SET TRANSACTION READ ONLY')
        if c.execute('SELECT version,checksum FROM schema_version ORDER BY version').fetchall()!=expected:
            raise ValueError('The installed database is incompatible with this package')
    for name,row in plan['targets'].items():
        relative(name)
        if set(row)!={'before','after'} or any(not isinstance(row[k],str) or len(row[k])!=64
                                              or any(ch not in '0123456789abcdef' for ch in row[k]) for k in row):
            raise ValueError('Bind both old and new application bytes')
        if digest(trusted(source/'programs'/name).read_bytes())!=row['after']:
            raise ValueError('A qualified candidate source changed')
        installed=digest(trusted(ROOT/name).read_bytes())
        if installed not in ({row['before'],row['after']} if allow_recovery else {row['before']}):
            raise ValueError('The installed application preimage differs')
        if plan['mode']=='live' and not live_path(name):
            raise ValueError('This component is not approved for the live-update path')
    qualification = plan['qualification']
    if set(qualification)!={'sha256'}:
        raise ValueError('Bind the complete qualification receipt')
    evidence_body = trusted(source/'qualification.json').read_bytes()
    if digest(evidence_body)!=qualification['sha256']:
        raise ValueError('The qualification receipt changed')
    evidence = json.loads(evidence_body)
    if (evidence.get('passed') is not True or evidence.get('cleanup') is not True
            or evidence.get('schema_unchanged') is not True
            or evidence.get('sources_unchanged') is not True or type(evidence.get('tests')) is not int
            or evidence['tests']<1 or any(evidence.get(key)!=0 for key in ('errors','failures','skipped'))
            or evidence.get('targets')!={name:row['after'] for name,row in plan['targets'].items()}):
        raise ValueError('The qualification does not cover this exact candidate')
    if plan['mode']=='live' and not all(evidence.get(key) is True for key in (
            'same_container_verified','frozen_attempts_verified','controller_switch_verified')):
        raise ValueError('Live updates need candidate-specific running-work qualification')
    return plan


def command(*args, seconds=180):
    value = subprocess.run(list(map(str,args)),capture_output=True,text=True,timeout=seconds,
        env=dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin',LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1'))
    if value.returncode: raise RuntimeError('A fixed application-update operation failed')
    return value.stdout


def atomic(path, body):
    trusted(path)
    fd,name=tempfile.mkstemp(prefix='.application-update-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(body);stream.flush();os.fsync(stream.fileno())
        os.chmod(name,0o644);os.chown(name,0,0);os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:Path(name).unlink(missing_ok=True)


class Installation:
    def __init__(self, options, queue, source, plan):
        self.options,self.queue,self.source,self.plan=options,queue,Path(source),plan
        self.previous={};self.snapshot=None;self.single=None;self.containers={};self.changed=[]
        self.settings=None
        self.failure=None
        self.backup_fd=None
        self.public_stopped=self.private_stopped=False

    def save_progress(self):
        value=dict(format=FORMAT,plan=digest(trusted(self.source/'plan.json').read_bytes()),
            snapshot=self.snapshot,single=self.single,settings=self.settings,containers=self.containers,
            public_stopped=self.public_stopped,private_stopped=self.private_stopped,changed=self.changed)
        if self.failure is not None:value['failure']=self.failure
        body=json.dumps(value,sort_keys=True,default=str).encode()
        path=self.source/'install-progress.json'
        if not path.exists():
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
        atomic(path,body);path.chmod(0o600)

    def load_progress(self):
        path=self.source/'install-progress.json'
        value=json.loads(trusted(path).read_bytes())
        if value.get('format')!=FORMAT or value.get('plan')!=digest(trusted(self.source/'plan.json').read_bytes()):
            raise ValueError('The recovery receipt does not match the candidate')
        if (not isinstance(value.get('changed'),list) or len(value['changed'])>len(self.plan['targets'])
                or not set(value['changed'])<=set(self.plan['targets'])):
            raise ValueError('Invalid interrupted-publication receipt')
        for key in ('snapshot','single','settings','containers','public_stopped','private_stopped','changed'):
            setattr(self,key,value[key])
        self.failure=value.get('failure')
        for name in self.changed:
            before=trusted(self.source/'rollback'/name).read_bytes()
            if digest(before)!=self.plan['targets'][name]['before']:
                raise ValueError('A rollback preimage differs')
            self.previous[name]=before

    def guarded(self, phase, callback):
        """Preserve bounded failure metadata without copying exception contents."""
        if phase not in ('install','verify','rollback'):raise ValueError('Invalid installation phase')
        def invoke():
            try:return callback()
            except BaseException as error:
                frame=error.__traceback__
                while frame and frame.tb_next:frame=frame.tb_next
                self.failure=dict(phase=phase,error_type=type(error).__name__)
                if frame is not None:
                    self.failure['callsite']=dict(file=Path(frame.tb_frame.f_code.co_filename).name,
                        function=frame.tb_frame.f_code.co_name,line=frame.tb_lineno)
                try:self.save_progress()
                except BaseException:pass  # Preserve the original refusal if evidence cannot be written.
                raise
        return invoke

    def metadata(self):
        # Fields that must not change when a controller reconnects. Completion,
        # normal progress and permitted resource growth are deliberately excluded.
        with self.queue._connection() as c:
            c.execute('SET TRANSACTION READ ONLY')
            attempts=c.execute('''SELECT a.id,a.job_id,a.number,a.execution_key,a.deadline,
                a.initial_resources,a.started_at,j.configuration_id,j.model_digest,j.prepared_fingerprint
                FROM attempts a JOIN jobs j ON j.id=a.job_id WHERE a.pool_id=%s ORDER BY a.id LIMIT 10001''',
                (self.queue.pool_id,)).fetchall()
            pool=c.execute('''SELECT cpu_millis,memory_mib,storage_mib,hold_cpu,hold_memory,hold_storage,
                processing_cpu,processing_memory,processing_storage,processing_configured,
                growth_memory,idle_interactive_cpu,policy FROM pools WHERE id=%s''',
                (self.queue.pool_id,)).fetchone()
        if len(attempts)>10000:raise ValueError('Review a larger update metadata budget')
        return dict(attempts=attempts,pool=pool)

    def single_identity(self):
        return {unit:command('systemctl','show',unit,'--property=MainPID','--property=InvocationID',
                            '--property=ActiveState') for unit in SINGLE}

    def backup_idle(self):
        state=command('systemctl','show','simpaths-backup.service','--property=ActiveState','--value').strip()
        if state not in ('inactive','failed'):raise ValueError('Wait for the current backup without stopping it')

    def ready(self):
        state=command('systemctl','show','simpaths-backup.service','--property=ActiveState','--value').strip()
        return state in ('inactive','failed')

    def service_ready(self, *, start_public=True):
        script="""import sys
sys.path[:0]=['/opt/simpaths-online/SimPaths','/opt/simpaths-online/JAS-mine-web']
from deploy.multirun.vm_frontend import main
raise SystemExit(main(['preflight','--config','/etc/simpaths-online/public.toml']))
"""
        until=time.monotonic()+120
        while True:
            try:
                command('runuser','-u','simpaths-web','--',ROOT/'venv/bin/python','-I','-B','-c',script,seconds=20)
                break
            except RuntimeError:
                if time.monotonic()>=until:raise
                time.sleep(1)
        if start_public:command('systemctl','start',PUBLIC)
        for port,path,expected in ((443,'/healthz',dict(ready=True)),(8443,'/health',dict(status='ok',python='ok'))):
            until=time.monotonic()+120
            while True:
                try:
                    body=command('curl','--disable','--silent','--show-error','--fail','--noproxy','*',
                        '--max-time','10','--max-filesize','65536',f'https://13.140.163.1:{port}{path}',seconds=15)
                    if json.loads(body)!=expected:raise RuntimeError('Unexpected application health response')
                    break
                except (RuntimeError,ValueError):
                    if time.monotonic()>=until:raise
                    time.sleep(1)

    def backup_fence(self):
        # The normal file-retention shared lock excludes an online capture, but
        # leaves SingleRun's database guard and ordinary retention readers intact.
        path=Path(self.options.state)/'.backup-files.lock'
        if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Invalid backup fence')
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_mode&0o077
                    or info.st_uid!=pwd.getpwnam('simpaths-online').pw_uid):
                raise ValueError('Invalid service backup fence')
            fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BaseException:
            os.close(fd);raise
        self.backup_fd=fd

    def release_fence(self):
        if self.backup_fd is not None:
            os.close(self.backup_fd);self.backup_fd=None

    def install(self):
        self.backup_idle()
        for unit in (PRIVATE,PUBLIC):
            if command('systemctl','show',unit,'--property=ActiveState','--value').strip()!='active':
                raise ValueError('Preserve a service already requiring operator attention')
        self.backup_fence()
        self.single=self.single_identity()
        self.settings=settings_snapshot(self.options)
        self.save_progress()
        self.public_stopped=True;self.save_progress();command('systemctl','stop',PUBLIC)
        self.private_stopped=True;self.save_progress();command('systemctl','stop',PRIVATE,seconds=240)
        self.backup_idle()
        self.snapshot=self.metadata()
        if self.plan['mode']=='live':
            with self.queue._connection() as c:
                c.execute('SET TRANSACTION READ ONLY')
                active=c.execute("SELECT execution_key FROM attempts WHERE pool_id=%s AND phase<>'finished' LIMIT 1001",
                                 (self.queue.pool_id,)).fetchall()
            if len(active)>1000:raise ValueError('Review a larger running-container bound')
            for row in active:
                key=row['execution_key']
                self.containers[key]=command('docker','inspect','--format','{{.Id}}','jasmine-'+key).strip()
        self.save_progress()
        for name,row in self.plan['targets'].items():
            path=ROOT/name;before=trusted(path).read_bytes()
            if digest(before)!=row['before']:raise ValueError('The installed preimage changed during drain')
            previous=self.source/'rollback'/name;previous.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            if previous.exists():
                if trusted(previous).read_bytes()!=before:raise ValueError('Preserve an unexpected rollback preimage')
            else:
                with previous.open('xb') as stream:
                    stream.write(before);stream.flush();os.fsync(stream.fileno())
                previous.chmod(0o600)
            self.previous[name]=before;self.changed.append(name)
            self.save_progress()
            atomic(path,trusted(self.source/'programs'/name).read_bytes())
        command('systemctl','start',PRIVATE)
        self.service_ready()

    def verify(self):
        for name,row in self.plan['targets'].items():
            if digest(trusted(ROOT/name).read_bytes())!=row['after']:raise ValueError('Candidate bytes differ')
        return self.verify_common()

    def verify_common(self):
        current=self.metadata()
        encode=lambda value:json.dumps(value,sort_keys=True,default=str)
        if encode(current)!=encode(self.snapshot):raise ValueError('Attempt identities, deadlines or pool policy changed')
        if self.single_identity()!=self.single:raise ValueError('SingleRun changed during the MultiRun update')
        settings=settings_snapshot(self.options)
        if settings!=self.settings:raise ValueError('Private configuration changed')
        for key,identifier in self.containers.items():
            # Finished work can be cleaned normally; unfinished work must still
            # be backed by the exact original container, without redispatch.
            with self.queue._connection() as c:
                row=c.execute('SELECT phase FROM attempts WHERE pool_id=%s AND execution_key=%s',
                              (self.queue.pool_id,key)).fetchone()
            if row and row['phase']!='finished':
                if command('docker','inspect','--format','{{.Id}}','jasmine-'+key).strip()!=identifier:
                    raise ValueError('A running simulation container was replaced')
        self.service_ready()
        self.release_fence()
        return digest(json.dumps(current,sort_keys=True,default=str).encode())

    def rollback(self):
        if self.snapshot is None:
            # Never start an operator-stopped unit after a preflight refusal.
            if not self.public_stopped and not self.private_stopped:
                for unit in (PRIVATE,PUBLIC):
                    if command('systemctl','show',unit,'--property=ActiveState','--value').strip()!='active':
                        raise ValueError('An unchanged service still requires operator attention')
                for name,row in self.plan['targets'].items():
                    if digest(trusted(ROOT/name).read_bytes())!=row['before']:
                        raise ValueError('Unexpected programs need explicit recovery')
                self.service_ready(start_public=False)
                self.release_fence()
                return digest(b'verified-no-publication-active-services')
            if self.private_stopped:command('systemctl','start',PRIVATE)
            self.service_ready()
            if self.single is not None and self.single_identity()!=self.single:
                raise ValueError('SingleRun changed before application publication')
            self.release_fence()
            return digest(b'verified-pre-publication-recovery')
        command('systemctl','stop',PUBLIC);command('systemctl','stop',PRIVATE,seconds=240)
        for name in reversed(self.changed):atomic(ROOT/name,self.previous[name])
        command('systemctl','start',PRIVATE);self.service_ready()
        return self.verify_common()


def build_callbacks(options, queue, source, plan):
    if os.geteuid()!=0:raise ValueError('Only the host operator may install application updates')
    installation=Installation(options,queue,source,plan)
    return dict(install=installation.guarded('install',installation.install),
                verify=installation.guarded('verify',installation.verify),
                rollback=installation.guarded('rollback',installation.rollback),ready=installation.ready)


def recovery_callbacks(options, queue, source, plan):
    if os.geteuid()!=0:raise ValueError('Only the host operator may recover application updates')
    installation=Installation(options,queue,source,plan)
    if (Path(source)/'install-progress.json').exists():
        installation.load_progress()
    elif any(digest(trusted(ROOT/name).read_bytes())!=row['before'] for name,row in plan['targets'].items()):
        raise ValueError('Changed programs have no durable recovery receipt')
    return dict(rollback=installation.guarded('rollback',installation.rollback))
