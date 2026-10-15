#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Run isolated, bounded Visualiser capacity stages with whole-cgroup measurements.
# @author ross richardson
"""Container-only capacity measurement; no simulations, database or HTTP service."""
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

MIB=1024**2
FORMAT='simpaths.visualiser.capacity-fixture.v1'
CGROUP=Path('/sys/fs/cgroup')
NOTICE=('Private capacity fixture: repeated source summaries are not independent '
        'random seeds and do not validate scientific uncertainty.')


def invalid_number(_):
    raise ValueError('Nonfinite JSON number')


def decode(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    return json.loads(raw,parse_constant=invalid_number,object_pairs_hook=unique)


def write_json(path,value):
    raw=json.dumps(value,sort_keys=True,indent=2,allow_nan=False).encode()+b'\n'
    pending=path.with_name('.'+path.name+'.pending')
    fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as target:target.write(raw);target.flush();os.fsync(target.fileno())
        os.replace(pending,path)
    finally:pending.unlink(missing_ok=True)


@contextmanager
def safe_open(root,name):
    if (not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,240}',name)
            or any(part in ('','.','..') for part in name.split('/')) or name.startswith('/')):
        raise ValueError('Invalid source path')
    directory=os.open(Path(root),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        parts=name.split('/')
        for part in parts[:-1]:
            new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=new
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as source:
            info=os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('Invalid source file')
            yield source
    finally:os.close(directory)


def receipt(root,name,maximum):
    with safe_open(root,name) as source:
        before=os.fstat(source.fileno())
        if before.st_size>maximum:raise ValueError('Source exceeds bound')
        digest=hashlib.sha256()
        while chunk:=source.read(MIB):digest.update(chunk)
        after=os.fstat(source.fileno())
        if (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise ValueError('Source changed during verification')
        return dict(bytes=before.st_size,sha256=digest.hexdigest())


def load_small(root,name,maximum=MIB):
    with safe_open(root,name) as source:
        raw=source.read(maximum+1)
    if len(raw)>maximum:raise ValueError('JSON source exceeds bound')
    return decode(raw)


def load_fixture(fixture,build,raw_root):
    manifest=load_small(fixture,'manifest.json')
    if manifest.get('format')!=FORMAT or manifest.get('mode') not in ('summary-replay','raw-native'):
        raise ValueError('Unknown capacity fixture')
    mode=manifest['mode'];seeds=manifest.get('seeds');years=manifest.get('annual_years')
    if (not isinstance(seeds,list) or not 1<=len(seeds)<=10 or any(type(s) is not str or not re.fullmatch('[0-9]{1,20}',s) for s in seeds)
            or len(set(seeds))!=len(seeds) or years!=list(range(2019,2071))):
        raise ValueError('Fixture needs 1..10 seed labels and all 52 annual years')
    configurations=manifest.get('configurations')
    if not isinstance(configurations,list) or not 2<=len(configurations)<=10:
        raise ValueError('Need a baseline and bounded alternatives')
    ids=set();used=set()
    for index,configuration in enumerate(configurations):
        role='baseline' if index==0 else 'scenario_'+str(index)
        cid=configuration.get('id');name=configuration.get('name');runs=configuration.get('runs')
        if (not isinstance(cid,str) or not re.fullmatch('[A-Za-z0-9_-]{1,100}',cid) or cid in ids
                or not isinstance(name,str) or not 1<=len(name)<=200 or any(ord(c)<32 for c in name)
                or configuration.get('role')!=role or not isinstance(runs,list)
                or [r.get('seed') for r in runs]!=seeds):raise ValueError('Invalid configuration identity')
        ids.add(cid)
        for run in runs:
            if mode=='summary-replay':
                if not re.fullmatch('[0-9]{1,20}',str(run.get('source_seed',''))):raise ValueError('Missing source seed')
                used.add(run.get('summary'))
            else:
                used.update([run.get('person'),run.get('benefit')])
    files=manifest.get('files')
    if not isinstance(files,dict) or set(files)!=used:raise ValueError('Source inventory differs')
    root=fixture if mode=='summary-replay' else raw_root
    maximum=64*MIB if mode=='summary-replay' else 8*1024*MIB
    for name,entry in files.items():
        if (not isinstance(entry,dict) or set(entry)!={'bytes','sha256'} or
                type(entry['bytes']) is not int or entry['bytes']<1 or
                not re.fullmatch('[a-f0-9]{64}',str(entry['sha256'])) or receipt(root,name,maximum)!=entry):
            raise ValueError('Source checksum differs')
    built=load_small(build,'build.json')
    if built.get('format')!='simpaths.visualiser.build.v1' or built.get('mode')!='development-paired':
        raise ValueError('Need a pinned paired Visualiser build')
    if not isinstance(built.get('files'),dict) or not {'runner.cjs','calculation.cjs'}<=set(built['files']):
        raise ValueError('Incomplete pinned build')
    for name,digest in built['files'].items():
        if not re.fullmatch('[A-Za-z0-9_.-]{1,100}',name) or receipt(build,name,8*MIB)['sha256']!=digest:
            raise ValueError('Build checksum differs')
    backend=manifest.get('backend',{})
    for name in ('runner','calculation'):
        if backend.get(name+'_sha256')!=built['files'][name+'.cjs']:raise ValueError('Fixture and build differ')
    return manifest,built


def counters(path):
    result={}
    for line in path.read_text().splitlines():
        fields=line.split()
        if len(fields)!=2 or not fields[1].isdigit() or fields[0] in result:raise ValueError('Invalid cgroup counters')
        result[fields[0]]=int(fields[1])
    return result


def require_limits(memory_mib,root=CGROUP,*,cpu_millis=1000):
    if memory_mib not in (2048,3072,4096):raise ValueError('Unsupported memory case')
    if cpu_millis not in (1000,2000):raise ValueError('Unsupported CPU case')
    memory=(root/'memory.max').read_text().strip();swap=(root/'memory.swap.max').read_text().strip()
    cpu=(root/'cpu.max').read_text().split()
    if memory!=str(memory_mib*MIB) or swap!='0' or len(cpu)!=2 or not all(c.isdigit() for c in cpu) or int(cpu[0])*1000!=int(cpu[1])*cpu_millis or int(cpu[1])<1:
        raise ValueError('Require exact cgroup v2 memory/CPU limits and no swap')
    return dict(memory_max_bytes=int(memory),memory_swap_max_bytes=0,cpu_max=[int(c) for c in cpu])


def snapshot(root=CGROUP):
    current=int((root/'memory.current').read_text());peak=int((root/'memory.peak').read_text())
    memory=counters(root/'memory.stat');events=counters(root/'memory.events');cpu=counters(root/'cpu.stat')
    if ('inactive_file' not in memory or not {'oom','oom_kill','max'}<=set(events)
            or not {'usage_usec','nr_periods','nr_throttled','throttled_usec'}<=set(cpu)):
        raise ValueError('Required cgroup metrics unavailable')
    io={}
    for line in (root/'io.stat').read_text().splitlines():
        fields=line.split();values={}
        if not re.fullmatch('[0-9]+:[0-9]+',fields[0]):raise ValueError('Invalid I/O device')
        for field in fields[1:]:
            key,value=field.split('=')
            if not value.isdigit():raise ValueError('Invalid I/O counter')
            values[key]=int(value)
        if not {'rbytes','wbytes','rios','wios'}<=set(values):raise ValueError('Missing I/O counters')
        io[fields[0]]=values
    pressure={}
    for kind in ('cpu','memory','io'):
        values={}
        for line in (root/(kind+'.pressure')).read_text().splitlines():
            fields=line.split();raw=dict(field.split('=') for field in fields[1:])
            if not raw.get('total','').isdigit():raise ValueError('Invalid pressure counter')
            values[fields[0]]=int(raw['total'])
        if 'some' not in values:raise ValueError('Missing pressure metrics')
        pressure[kind]=values
    if current<0 or peak<current:raise ValueError('Invalid memory metrics')
    return dict(memory_current_bytes=current,memory_peak_bytes=peak,
        working_bytes=max(0,current-memory['inactive_file']),memory_events=events,cpu=cpu,io=io,pressure_usec=pressure)


class Sampler:
    def __init__(self,path,root=CGROUP,interval=.25):
        self.path=path;self.root=root;self.interval=interval;self.event=threading.Event()
        self.lock=threading.Lock();self.stage='verification';self.samples=0;self.errors=[];self.peaks={}

    def capture(self):
        value=snapshot(self.root)
        with self.lock:
            value.update(stage=self.stage,elapsed_seconds=round(time.monotonic()-self.started,4),
                         wall_time_ns=time.time_ns())
            self.samples+=1
            if self.samples>200000:raise ValueError('Sample budget exhausted')
            peaks=self.peaks.setdefault(self.stage,dict(memory_current_bytes=0,working_bytes=0))
            for field in peaks:peaks[field]=max(peaks[field],value[field])
            self.target.write(json.dumps(value,sort_keys=True,allow_nan=False)+'\n');self.target.flush()
        return value

    def start(self):
        self.started=time.monotonic();self.target=self.path.open('x',encoding='utf8')
        try:self.capture()
        except BaseException:self.target.close();raise
        def loop():
            while not self.event.wait(self.interval):
                try:self.capture()
                except BaseException as error:self.errors.append(type(error).__name__);self.event.set()
        self.thread=threading.Thread(target=loop,daemon=True);self.thread.start();return self

    def stop(self):
        self.event.set();self.thread.join(5)
        if self.thread.is_alive():raise RuntimeError('Sampler did not stop')
        try:
            if self.errors:raise RuntimeError('Required metrics unavailable: '+','.join(self.errors))
            return self.capture()
        finally:self.target.close()


def command(log,argv,*,pass_fds=()):
    child=subprocess.Popen(['timeout','--signal=TERM','--kill-after=5','3600',*map(str,argv)],
        pass_fds=pass_fds,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,
        env={'PATH':os.defpath,'LANG':'C.UTF-8'})
    try:
        result=child.wait(timeout=3610)
        if result:raise subprocess.CalledProcessError(result,argv)
    except BaseException:
        # Reap the whole private command group before deleting its scratch files.
        try:os.killpg(child.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            child.wait(timeout=10)
        raise


def validate_rows(paths,manifest):
    counts=[];paired=0;nonzero=0;complete_pairs=0;expected=len(manifest['seeds'])
    for index,path in enumerate(paths):
        if path.stat().st_size>96*MIB:raise ValueError('Aggregate file exceeds budget')
        rows=decode(path.read_bytes());counts.append(len(rows))
        if sorted({r['year'] for r in rows})!=manifest['annual_years']:raise ValueError('Aggregate annual coverage differs')
        for row in rows:
            n_runs=row['n_runs']
            if type(n_runs) is not int or not 1<=n_runs<=expected:raise ValueError('Aggregate repetition count differs')
            if manifest['mode']=='summary-replay' and n_runs!=expected:raise ValueError('Replay repetition count differs')
            n=row.get('paired_n_runs',0)
            if type(n) is not int or not 0<=n<=min(expected,n_runs):raise ValueError('Paired count differs')
            if manifest['mode']=='summary-replay' and n not in (0,expected):raise ValueError('Replay paired count differs')
            if index and n:
                paired+=1;complete_pairs+=n==expected;delta=row.get('paired_mean_delta')
                if delta is not None:nonzero+=abs(delta)>1e-10
    if not paired or not nonzero or not complete_pairs:raise ValueError('Expected nonzero paired policy impacts and complete seed coverage')
    return dict(rows_per_configuration=counts,paired_cells=paired,nonzero_paired_cells=nonzero,complete_seed_paired_cells=complete_pairs,
                annual_years=manifest['annual_years'],seed_labels=manifest['seeds'])


def run_pipeline(args,manifest,built,work,sampler,record):
    sys.path.insert(0,str(args.frontend))
    from jasmine_web.batch.aggregate_io import invoke
    from jasmine_web.batch.aggregate_limits import AggregateLimits
    from jasmine_web.batch.results import verified_file
    limits=AggregateLimits(96*MIB,256*MIB,200000,400000)
    record['publication_limits']=limits.describe()
    node=shutil.which('node')
    if not node:raise RuntimeError('Node is unavailable')
    version=subprocess.check_output([node,'--version'],timeout=10,stdin=subprocess.DEVNULL,
        env={'PATH':os.defpath,'LANG':'C.UTF-8'},text=True).strip()
    if not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+',version):raise ValueError('Invalid Node version')
    record['runtime']=dict(python=sys.version,node=version,kernel=platform.release(),machine=platform.machine())
    heap=max(512,args.memory_mib-1024);helper=args.memory_mib-256
    record.update(node_old_space_mib=heap,helper_address_space_mib=helper)
    metrics=[];outputs=[];replay=[]
    for index,configuration in enumerate(manifest['configurations']):
        outputs.append(dict(path=str(work/('aggregate-'+str(index)+'.json')),role=configuration['role']))
        for number,run in enumerate(configuration['runs']):
            path=work/('metrics-'+str(index)+'-'+str(number)+'.json');metrics.append(str(path))
            if manifest['mode']=='summary-replay':
                replay.append(dict(summary=str(args.fixture/run['summary']),source_seed=run['source_seed'],
                    seed=run['seed'],role=configuration['role'],output=str(path)))

    @contextmanager
    def stage(name):
        sampler.stage=name;before=sampler.capture();started=time.monotonic()
        result=dict(passed=False);record['stages'][name]=result
        try:yield;result['passed']=True
        except BaseException as error:
            result['error_type']=type(error).__name__
            raise
        finally:
            after=sampler.capture();result.update(elapsed_seconds=round(time.monotonic()-started,4),
                before=before,after=after,sampled_peaks=dict(sampler.peaks.get(name,{})))
            write_json(args.output/'report.json',record)

    with (args.output/'processing.log').open('xb') as log:
        def execute(argv,*,pass_fds=()):command(log,argv,pass_fds=pass_fds)
        def node_run(script,request):
            write_json(work/'request.json',request)
            execute([node,'--max-old-space-size='+str(heap),script,work/'request.json'])
        with stage('replay' if replay else 'parse'):
            if replay:node_run(Path(__file__).parent/'visualiser'/'capacity_replay.cjs',dict(runs=replay))
            else:
                position=0
                for configuration in manifest['configurations']:
                    for run in configuration['runs']:
                        with ExitStack() as stack:
                            sources=[stack.enter_context(verified_file(args.raw_root,dict(path=run[kind],**manifest['files'][run[kind]]))) for kind in ('person','benefit')]
                            initial=[os.fstat(s.fileno()) for s in sources]
                            request=dict(operation='run',person_fd=sources[0].fileno(),benefit_fd=sources[1].fileno(),
                                role=configuration['role'],run=run['seed'],output=metrics[position])
                            write_json(work/'request.json',request)
                            execute([node,'--max-old-space-size='+str(heap),args.build/'runner.cjs',work/'request.json'],
                                pass_fds=tuple(s.fileno() for s in sources))
                            for first,source in zip(initial,sources):
                                last=os.fstat(source.fileno())
                                if (first.st_size,first.st_mtime_ns,first.st_ctime_ns)!=(last.st_size,last.st_mtime_ns,last.st_ctime_ns):
                                    raise ValueError('Raw source changed during parsing')
                        position+=1
        with stage('aggregate'):
            node_run(args.build/'runner.cjs',dict(operation='aggregate',metrics=metrics,outputs=outputs))
        public=dict(format='simpaths.visualiser.v2',backend=manifest['backend'],
            experiment='Private capacity fixture' if replay else 'Retained native capacity measurement',
            configurations=[dict(id=c['id'],name=c['name'],role='Baseline' if i==0 else 'Scenario',
                dataset='capacity-fixture' if replay else 'verified-native-results',model='verified-pinned-model',
                runs=[dict(seed=r['seed']) for r in c['runs']]) for i,c in enumerate(manifest['configurations'])],
            comparison=dict(baseline=manifest['configurations'][0]['id'],scenarios=[c['id'] for c in manifest['configurations'][1:]]))
        paths=[Path(o['path']) for o in outputs];common=dict(memory_mib=helper,publication_limits=limits.describe())
        with stage('validate'):
            record['validation']=validate_rows(paths,manifest)
        with stage('publish'):
            counts=record['validation']['rows_per_configuration']
            if any(count>limits.rows_per_series for count in counts) or sum(counts)>limits.comparison_rows:
                record['failure_kind']='publication_row_limit'
                raise ValueError('Comparison exceeds the unchanged production row budget')
            with ExitStack() as stack:
                sources=[stack.enter_context(path.open('rb')) for path in paths]
                body=invoke(dict(common,operation='publish',format=public['format'],public=public,kind='rows',
                    notice=NOTICE if replay else 'Verified native outputs; no simulations were rerun.',comparison_available=True),
                    tuple(source.fileno() for source in sources),execute)
            publication=work/'published.json';publication.write_bytes(body)
            published=dict(bytes=len(body),sha256=hashlib.sha256(body).hexdigest());del body
            record['publication']=published;record['publication_sha256']=published['sha256']
        views=work/'views';views.mkdir(mode=0o700)
        with ExitStack() as stack:
            directory=os.open(views,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);stack.callback(os.close,directory)
            with stage('index'):
                with publication.open('rb') as source:
                    indexed=decode(invoke(dict(common,operation='index',format=public['format'],sha256=published['sha256']),
                        (source.fileno(),directory),execute))
                record['index']=indexed
            request=dict(common,sha256=published['sha256'],index_sha256=indexed['index_sha256'])
            with (views/'index.json').open('rb') as source:
                with stage('catalogue'):
                    body=invoke(dict(request,operation='catalogue'),(source.fileno(),directory),execute)
                    catalogue=decode(body)
                    if catalogue['seeds']!=manifest['seeds']:raise ValueError('Catalogue seed metadata differs')
                    record['catalogue']=dict(bytes=len(body),sha256=hashlib.sha256(body).hexdigest());del body
                with stage('view'):
                    selection=dict(variable='Highest Level of Education',stratifier='Overall',kind='levels',
                        configurations=[c['id'] for c in manifest['configurations']])
                    body=invoke(dict(request,operation='view',selection=selection),(source.fileno(),directory),execute)
                    view=decode(body)
                    if (view.get('format')!='simpaths.visualiser.view.v1' or not view.get('series')
                            or any(sorted({r['year'] for r in s['rows']})!=manifest['annual_years'] for s in view['series'])):
                        raise ValueError('Representative view coverage differs')
                    record['view']=dict(bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                        rows=sum(len(s['rows']) for s in view['series']),selection=selection)
                    record['view_sha256']=record['view']['sha256']


def execute(args):
    os.umask(0o077)
    if args.output.is_symlink():raise ValueError('Output must not be a symlink')
    args.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    if any(args.output.iterdir()) or args.output.stat().st_mode&0o077:raise ValueError('Choose an empty private output directory')
    work=args.output/'private';work.mkdir(mode=0o700)
    (args.output/'COPYRIGHT.md').write_text('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Generated private Visualiser capacity measurements and checksums.\n@author ross richardson\n-->\n')
    record=dict(format='simpaths.visualiser.capacity-worker.v1',passed=False,cleanup=False,stages={},
        model_executions=False,http_permission_test=False,memory_mib=args.memory_mib,
        started_wall_time_ns=time.time_ns(),cpu_millis=getattr(args,'cpu_millis',1000))
    write_json(args.output/'report.json',record)
    sampler=None;started=time.monotonic()
    try:
        record['limits']=require_limits(args.memory_mib,cpu_millis=getattr(args,'cpu_millis',1000))
        sampler=Sampler(args.output/'samples.jsonl').start();before=snapshot()
        record['initial_metrics']=before
        verification_started=time.monotonic()
        manifest,built=load_fixture(args.fixture,args.build,args.raw_root)
        manifest_receipt=receipt(args.fixture,'manifest.json',MIB);build_receipt=receipt(args.build,'build.json',MIB)
        record.update(mode=manifest['mode'],fixture_manifest=manifest_receipt,build_manifest=build_receipt,
            backend=manifest['backend'],provenance=manifest.get('provenance',{}),
            unique_source_bytes=sum(entry['bytes'] for entry in manifest['files'].values()),
            logical_input_bytes=sum(manifest['files'][run[key]]['bytes']
                for config in manifest['configurations'] for run in config['runs']
                for key in (('summary',) if manifest['mode']=='summary-replay' else ('person','benefit'))),
            limitations=([NOTICE,'Raw-file parsing and independent-seed variation are not measured.'] if manifest['mode']=='summary-replay' else
                ['Local capacity evidence does not establish VPS concurrency or HTTP permission behaviour.']))
        record['verification_before_seconds']=time.monotonic()-verification_started
        if getattr(args,'layout','monolithic')=='bounded-pairs':
            if __package__:
                from . import visualiser_capacity_bounded as bounded
            else:
                import visualiser_capacity_bounded as bounded
            bounded.run_tasks(args,manifest,built,work,sampler,record,sys.modules[__name__])
        else:
            run_pipeline(args,manifest,built,work,sampler,record)
        sampler.stage='verification'
        verification_started=time.monotonic()
        final_manifest,final_build=load_fixture(args.fixture,args.build,args.raw_root)
        if (final_manifest!=manifest or final_build!=built or receipt(args.fixture,'manifest.json',MIB)!=manifest_receipt
                or receipt(args.build,'build.json',MIB)!=build_receipt):raise ValueError('Inputs changed during trial')
        record['verification_after_seconds']=time.monotonic()-verification_started
        record['source_verification_passes']=2*len(args.tasks) if getattr(args,'layout','monolithic')=='bounded-pairs' else 2
        require_limits(args.memory_mib,cpu_millis=getattr(args,'cpu_millis',1000));after=sampler.stop();sampler=None
        record['metrics']=dict(before=before,after=after)
        if any(after['memory_events'][key]>before['memory_events'][key] for key in ('oom','oom_kill')):
            raise ValueError('Cgroup exhausted memory during trial')
        record.update(passed=True,sources_unchanged=True)
    except BaseException as error:
        record.update(passed=False,error_type=type(error).__name__)
        raise
    finally:
        if sampler is not None:
            try:record['final_metrics']=sampler.stop()
            except BaseException as error:record.update(passed=False,metrics_error=type(error).__name__)
        try:shutil.rmtree(work);record['cleanup']=True
        except BaseException as error:record.update(passed=False,cleanup_error=type(error).__name__)
        record['elapsed_seconds']=round(time.monotonic()-started,4)
        record['finished_wall_time_ns']=time.time_ns()
        write_json(args.output/'report.json',record)
    if not record['passed'] or not record['cleanup']:raise RuntimeError('Capacity trial incomplete')
    if getattr(args,'layout','monolithic')=='bounded-pairs':
        stable=dict(mode=record['mode'],fixture_manifest=record['fixture_manifest'],
            build_manifest=record['build_manifest'],tasks=[t['result_identity'] for t in record['tasks']])
    else:
        stable={key:record[key] for key in ('mode','fixture_manifest','build_manifest','publication','index','catalogue','view','validation')}
    write_json(args.output/'receipt.json',stable)
    print('PASSED: '+str(args.output/'report.json'),flush=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('fixture','build','frontend','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--raw-root',type=Path,default=Path('/raw'))
    parser.add_argument('--memory-mib',type=int,choices=(2048,3072,4096),required=True)
    parser.add_argument('--cpu-millis',type=int,choices=(1000,2000),default=1000)
    parser.add_argument('--layout',choices=('monolithic','bounded-pairs'),default='monolithic')
    parser.add_argument('--tasks',type=int,nargs='+',default=[0])
    parser.add_argument('--cache',type=Path,default=Path('/cache'))
    parser.add_argument('--view-reads',type=int,choices=range(1,21),default=5)
    args=parser.parse_args(argv)
    if (not 1<=len(args.tasks)<=8 or len(set(args.tasks))!=len(args.tasks)
            or any(not 0<=n<8 for n in args.tasks)):
        parser.error('Use one to eight distinct task IDs in 0..7')
    for name in ('fixture','build','frontend'):setattr(args,name,getattr(args,name).resolve(strict=True))
    args.output=args.output.absolute();args.raw_root=args.raw_root.absolute()
    def interrupted(_signum,_frame):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    try:execute(args);return 0
    except (Exception,KeyboardInterrupt) as error:
        print('FAILED: '+type(error).__name__+'; inspect the private report.',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
