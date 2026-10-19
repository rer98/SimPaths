"""(C) Copyright 2026, by Ross Richardson

Pinned Visualiser adapter: private VM aggregation, approved vocabulary and charts.
Own-input/public previews use upstream seed-paired multi-scenario calculations;
restricted provider results still require approved disclosure controls.
@author ross richardson
"""
from contextlib import ExitStack, contextmanager, nullcontext
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time


class VisualiserBackend:
    supports_parallel_seeds=True
    supports_source_ready=True
    # Explicit public resources from the trusted application, never a public-dir
    # glob that could also expose bundled simulation data or server calculations.
    APPLICATION_ASSETS=frozenset({'index.html','visualiser.js','visualiser.css',
        'pmh_logo.png','UKRILogo.png','Interpreting-results.html','Interpreting-results.css',
        'citation.html','citation.css','SimPaths-logo-transparent.png'})

    def __init__(self, build, execution_root, *, public_datasets=(), memory_mib=1024,
                 summary_cache_root=None, summary_cache_bytes=2*1024**3,
                 summary_cache_entries=256):
        from jasmine_web.batch.results import open_output
        self.build=Path(build).resolve(strict=True)
        self.root=Path(execution_root)
        self.memory=memory_mib
        if memory_mib<256 or memory_mib>16384:
            raise ValueError('Visualiser memory must be 256–16384 MiB')
        self.node=shutil.which('node')
        if not self.node or not shutil.which('timeout'):
            raise ValueError('Visualiser processing requires Node and GNU timeout')
        version=subprocess.check_output([self.node,'--version'],text=True,timeout=10,
            env={'PATH':os.defpath,'LANG':'C.UTF-8'}).strip()
        if not re.fullmatch(r'v\d+\.\d+\.\d+',version) or int(version[1:].split('.')[0])<18:
            raise ValueError('Visualiser processing requires Node 18 or later')
        with open_output(self.build,'build.json') as source:
            manifest=json.loads(source.read(1024**2))
        if (manifest.get('format')!='simpaths.visualiser.build.v1' or
                manifest.get('mode') not in ('development-levels','development-paired')):
            raise ValueError('Use a reviewed development Visualiser build')
        if not re.fullmatch('[a-f0-9]{40}',manifest['revision']):
            raise ValueError('Invalid Visualiser revision')
        required={'index.html','visualiser.js','visualiser.css','runner.cjs','calculation.cjs'}
        if not required<=set(manifest['files']):
            raise ValueError('Incomplete Visualiser build')
        self.assets={}
        for name,digest in manifest['files'].items():
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',name):
                raise ValueError('Invalid Visualiser asset')
            with open_output(self.build,name) as source:
                content=source.read(8*1024**2+1)
            if len(content)>8*1024**2 or hashlib.sha256(content).hexdigest()!=digest:
                raise ValueError('Visualiser build has changed')
            if (name in self.APPLICATION_ASSETS or
                    re.fullmatch(r'\d+\.visualiser\.js',name) or name.endswith('.LICENSE.txt')):
                self.assets[name]=content
        self.manifest=manifest
        self.supports_summary_cache=(b'simpaths.visualiser.role-independent-metrics.v1'
                                     in (self.build/'runner.cjs').read_bytes())
        self.summary_cache=None
        self.source_checks=OrderedDict()
        self.source_check_mutex=threading.Lock()
        if summary_cache_root is not None:
            if not self.supports_summary_cache:
                raise ValueError('Summary reuse requires a reviewed build with role-independent metrics')
            from .visualiser_summary_cache import SummaryCache
            self.summary_cache=SummaryCache(summary_cache_root, maximum_bytes=summary_cache_bytes,
                                            maximum_entries=summary_cache_entries)
        from .visualiser_summary_holds import SummaryCapacityError
        self.supports_summary_retention=self.summary_cache is not None
        self.summary_retention_seconds=7*86400
        self.capacity_errors=(SummaryCapacityError,)
        self.paired=manifest['mode']=='development-paired'
        # Advertise a set only when the verified application bundle includes
        # its v2 reader. Older pinned builds still support the original pair.
        self.supports_selective_views=b'simpaths.visualiser.catalogue.v1' in self.assets['visualiser.js']
        self.supports_comparison_sets=self.supports_selective_views or b'simpaths.visualiser.v2' in self.assets['visualiser.js']
        self.public_datasets=frozenset(public_datasets)
        self.identity=dict(build=manifest,publication='own-and-public-preview-v1',
                           public_datasets=sorted(self.public_datasets),node=version,memory_mib=memory_mib)

    def prune_summaries(self, *, owner=None, configuration=None, unless_held=False):
        if self.summary_cache is None:
            return dict(entries=0,bytes=0)
        return self.summary_cache.prune(owner=owner,configuration=configuration,unless_held=unless_held)

    def prune_source_summaries(self, *, owner, configuration, job, attempt, unless_held=False):
        """Retire one source without discarding another experiment's baseline."""
        if self.summary_cache is None:
            return dict(entries=0,bytes=0)
        return self.summary_cache.prune(owner=owner,configuration=configuration,job=job,
                                       attempt=attempt,unless_held=unless_held)

    def retain_summaries(self, owner, experiment, comparisons, *, owner_entries):
        """Retain authorised dependency plans, independently of worker resources."""
        from .visualiser_summary_holds import scope
        if self.summary_cache is None:
            return dict(admitted=0,deferred=0)
        return self.summary_cache.holds.replace(owner,experiment,comparisons,
            calculation=scope(dict(calculation={key:self.manifest['files'][key]
                for key in ('runner.cjs','calculation.cjs')},node=self.identity['node'])),
            owner_entries=min(owner_entries,self.summary_cache.maximum_entries))

    def summary_retention_experiments(self):
        if self.summary_cache is None:
            return []
        with self.summary_cache._lock('.cache.lock'):
            records=self.summary_cache.holds.load()
        return sorted({(r['owner'],r['experiment']) for r in records.values()})

    def storage_snapshot(self):
        """Bounded numeric occupancy and missing-summary promises, without paths."""
        cache=self.summary_cache
        if cache is None:
            return dict(bytes=0,entries=0,maximum_bytes=0,maximum_entries=1,
                        promised_bytes=0,promised_entries=0,deferred=0)
        with cache._lock('.cache.lock'):
            entries=cache._entries();holds=cache.holds.load()
            promised,count,_=cache.holds.occupancy(holds,entries)
            return dict(bytes=sum(e[2] for e in entries),entries=len(entries),
                maximum_bytes=cache.maximum_bytes,maximum_entries=cache.maximum_entries,
                promised_bytes=promised,promised_entries=count,
                deferred=sum(not record['admitted'] for record in holds.values()))

    def check_summary_dependencies(self, selection):
        """Reject deferred comparisons before catalogue/source hash passes."""
        if self.summary_cache is None:
            return
        with self.summary_cache._lock('.cache.lock'):
            records=self.summary_cache.holds.load()
            for job in selection['jobs']:
                for repetition in job['repetitions']:
                    identity=dict(owner=selection['owner'],configuration=job['configuration'],
                        seed=str(repetition['seed']),execution={k:job[k] for k in ('job','attempt')},
                        calculation={k:self.manifest['files'][k] for k in ('runner.cjs','calculation.cjs')},
                        node=self.identity['node'])
                    self.summary_cache.holds.require_admitted(identity,records)

    def _summary_identity(self, source, run, inputs):
        """Selection role and display names never identify scientific summaries."""
        configuration=source['configuration']
        owner=source.get('owner')
        if not isinstance(owner,str) or not owner or len(owner)>256:
            raise ValueError('Private summary reuse requires the verified owner identity')
        selected=source.get('identity',{})
        return dict(owner=owner,configuration=configuration['id'],seed=str(run['seed']),
                    model=configuration['model'],dataset=configuration['dataset'],
                    prepared=configuration.get('prepared_fingerprint'),
                    execution={key:selected[key] for key in ('job','attempt','execution_key','prepared')
                               if key in selected},
                    files=[{key:item[key] for key in ('path','bytes','sha256')} for item in inputs],
                    calculation={key:self.manifest['files'][key] for key in ('runner.cjs','calculation.cjs')},
                    node=self.identity['node'])

    def allowed(self,origins,owner):
        return bool(origins) and all(origin==['user',owner] or
            len(origin)==2 and origin[0]=='provider' and origin[1] in self.public_datasets
            for origin in origins)

    def _verify_code(self):
        from jasmine_web.batch.results import verified_file
        for name in ('runner.cjs','calculation.cjs'):
            file=self.build/name
            with verified_file(self.build,dict(path=name,bytes=file.stat().st_size,
                                              sha256=self.manifest['files'][name])):
                pass

    @contextmanager
    def _verified_source(self, entry, *, check=None):
        """Reuse a successful hash only for the exact unchanged open file.

        Metadata is held in bounded process memory, never trusted from disk or
        browser input. Replacement, size, modification/change time, expected
        hash or inode changes require a fresh hash. Restart starts empty.
        """
        from jasmine_web.batch.results import open_output, OutputUnavailable
        checkpoint=check or (lambda:None)
        checkpoint()
        if self.summary_cache is None and check is None:
            from jasmine_web.batch.results import verified_file
            with verified_file(self.root,entry) as source:
                yield source
            return
        with open_output(self.root,entry['path']) as source:
            def stamp():
                info=os.fstat(source.fileno())
                if info.st_size!=entry['bytes'] or info.st_nlink!=1:
                    raise OutputUnavailable()
                return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            initial=stamp()
            key=(entry['sha256'],initial)
            with self.source_check_mutex:
                known=self.summary_cache is not None and key in self.source_checks
                if known:self.source_checks.move_to_end(key)
            if not known:
                digest=hashlib.sha256()
                while chunk:=source.read(1024**2):
                    checkpoint();digest.update(chunk)
                if stamp()!=initial or digest.hexdigest()!=entry['sha256']:
                    raise OutputUnavailable()
                if self.summary_cache is not None:
                    with self.source_check_mutex:
                        self.source_checks[key]=True
                        self.source_checks.move_to_end(key)
                        while len(self.source_checks)>4096:self.source_checks.popitem(last=False)
            source.seek(0)
            yield source
            if stamp()!=initial:
                raise OutputUnavailable()

    def process(self,sources,work,command,progress,*,comparison_set=False,seed_workers=1,resource_budget=None,
                source_ready=None):
        """Dictionary compatibility for calculation tests and trusted tooling."""
        from jasmine_web.batch.visualiser import artifact
        files=self.process_files(sources,work,command,progress,comparison_set=comparison_set,
                                 seed_workers=seed_workers,resource_budget=resource_budget,source_ready=source_ready)
        result=dict(comparison_available=files.comparison_available,notice=files.notice)
        if comparison_set:
            result['series']=[dict(configuration=c['configuration']['id'],rows=artifact(path))
                              for c,path in zip(sources,files.paths)]
        else:
            result['rows']=artifact(files.paths[0])
        return result

    def _seed(self,configuration,run,output,role,command,heap):
        """One immutable run request; its files and descriptors are never shared."""
        from jasmine_web.batch.local_executor import atomic_json
        check=getattr(command,'check',None)
        if check is not None:check()
        config=configuration['configuration']
        selected=[f for f in configuration['files'] if f['name'].startswith(run['folder']+'/csv/')]
        def one(kind):
            files=[f for f in selected if kind in Path(f['name']).name.lower()]
            if len(files)!=1:raise ValueError('Need one person and benefit file per run')
            return files[0]
        inputs=[one('person'),one('benefit')]
        identity=self._summary_identity(configuration,run,inputs) if self.summary_cache is not None else None
        if self.summary_cache is not None:self.summary_cache.preflight_entry(identity)
        with ExitStack() as stack:
            retained=configuration.get('retained_only',False)
            if retained:
                from datetime import datetime
                if (self.summary_cache is None or
                        datetime.fromisoformat(config['completed_at']).timestamp()+self.summary_retention_seconds<=time.time()):
                    raise ValueError('Retained intermediate summaries have expired')
            descriptors=[] if retained else [stack.enter_context(self._verified_source(item,check=check)) for item in inputs]
            before=[os.fstat(f.fileno()) for f in descriptors]
            cached=(self.summary_cache.entry(identity,check=check,checkpoint=getattr(command,'checkpoint',None))
                    if self.summary_cache is not None else nullcontext(None))
            with cached as entry:
                cache_hit=entry is not None and entry.restore(output)
                review=getattr(command,'performance',lambda *a,**k:None)
                review('cache',hits=int(cache_hit),misses=int(not cache_hit),
                    input_bytes=sum(item['bytes'] for item in inputs),job=configuration.get('identity',{}).get('job'))
                if not cache_hit:
                    if retained:
                        raise ValueError('Retained summaries are unavailable; expired raw output cannot be reparsed')
                    request=output.with_suffix('.request.json')
                    atomic_json(request,dict(operation='run',person_fd=descriptors[0].fileno(),benefit_fd=descriptors[1].fileno(),
                        role='baseline' if entry is not None else role,run=run['seed'],output=str(output)))
                    measured=entry.raw_processing(sum(item['bytes'] for item in inputs)) if entry is not None else nullcontext()
                    with measured:
                        measured_start=time.monotonic_ns()
                        command([self.node,'--max-old-space-size='+str(heap),str(self.build/'runner.cjs'),str(request)],
                                pass_fds=tuple(f.fileno() for f in descriptors))
                        self._unchanged(before,descriptors)
                        review('cache',raw_completed=1,raw_elapsed_ns=time.monotonic_ns()-measured_start,
                            job=configuration.get('identity',{}).get('job'))
                    if entry is not None:entry.publish(output)
            self._unchanged(before,descriptors)
        return dict(path=str(output),role=role,seed=str(run['seed']),canonical=True) if self.summary_cache is not None else str(output)

    @staticmethod
    def _unchanged(before,descriptors):
        from jasmine_web.batch.results import OutputUnavailable
        for initial,source in zip(before,descriptors):
            after=os.fstat(source.fileno())
            if (initial.st_size,initial.st_mtime_ns,initial.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                raise OutputUnavailable()

    def _seeds(self,tasks,command,progress,workers,heap,source_ready=None):
        """Bound submissions, retain run order, report completion on the caller."""
        remaining=Counter(id(task[0]) for task in tasks)
        def completed(index):
            source=tasks[index][0];remaining[id(source)]-=1
            if remaining[id(source)]==0 and source_ready is not None:
                source_ready(str(source['identity']['job']))
        if workers==1:
            result=[]
            for index,task in enumerate(tasks):
                result.append(self._seed(*task,command,heap));progress(len(result));completed(index)
            return result
        executor=ThreadPoolExecutor(max_workers=workers,thread_name_prefix='visualiser-seed')
        pending={};next_task=0;done_count=0;result=[None]*len(tasks)
        try:
            while next_task<len(tasks) or pending:
                command.check()
                while next_task<len(tasks) and len(pending)<workers:
                    pending[executor.submit(self._seed,*tasks[next_task],command,heap)]=next_task;next_task+=1
                ready,_=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
                for future in ready:
                    index=pending.pop(future);result[index]=future.result()
                    done_count+=1;progress(done_count);completed(index)
            return result
        except BaseException:
            command.abort()
            raise
        finally:
            # Joining includes workers hashing inputs or waiting on a seed
            # cache lock. Their cooperative checks observe command.abort().
            executor.shutdown(wait=True,cancel_futures=True)

    def process_files(self,sources,work,command,progress,*,comparison_set=False,seed_workers=1,resource_budget=None,
                      source_ready=None):
        """Leave bounded JSON rows private for the platform's aggregate helper."""
        from jasmine_web.batch.aggregate_io import AggregateFiles
        from jasmine_web.batch.local_executor import atomic_json
        from jasmine_web.batch.policy import Resources
        if type(seed_workers) is not int or not 1<=seed_workers<=2:
            raise ValueError('Use one or two seed workers')
        if resource_budget is not None and not isinstance(resource_budget,Resources):
            raise ValueError('Use an admitted preparation resource budget')
        if seed_workers>1 and (not isinstance(resource_budget,Resources) or
                resource_budget.cpu_millis<2000 or resource_budget.memory_mib<5120 or
                not getattr(command,'parallel_safe',False) or not callable(getattr(command,'check',None))
                or not callable(getattr(command,'abort',None))):
            raise ValueError('Parallel seeds require an admitted 5 GiB / 2-vCPU group and cancellable commands')
        heap=max(128,self.memory-256) if resource_budget is None else max(128,resource_budget.memory_mib//seed_workers-1024)
        if comparison_set and not self.supports_comparison_sets:
            raise ValueError('This Visualiser build supports single/pair results only')
        self._verify_code()
        groups=[];tasks=[];series=[]
        for configuration_index,configuration in enumerate(sources):
            config=configuration['configuration']
            role=('baseline' if configuration_index==0 else 'scenario_'+str(configuration_index)) if comparison_set and self.paired else config['role'].lower()
            for index,run in enumerate(config['runs']):
                output=work/('configuration-'+str(configuration_index)+'-run-'+str(index)+'.json')
                tasks.append((configuration,run,output,role))
        prepared=self._seeds(tasks,command,progress,min(seed_workers,len(tasks)) if tasks else 1,heap,source_ready)
        getattr(command,'performance',lambda *a,**k:None)('comparisons',completed=len(prepared))
        offset=0
        for configuration_index,configuration in enumerate(sources):
            count=len(configuration['configuration']['runs'])
            configuration_metrics=prepared[offset:offset+count];offset+=count;groups.append(configuration_metrics)
            if comparison_set:
                output=work/('configuration-'+str(configuration_index)+'-aggregate.json')
                if not self.paired:
                    # Retained older builds aggregate one alternative at a time.
                    atomic_json(work/'request.json',dict(operation='aggregate',metrics=configuration_metrics,output=str(output)))
                    command([self.node,'--max-old-space-size='+str(heap),
                             str(self.build/'runner.cjs'),str(work/'request.json')])
                series.append(output)
        notice='Development preview using Visualiser revision '+self.manifest['revision'][:12]+(
            '. Policy impacts and uncertainty use runs with matching random seeds.' if self.paired else
            '. Baseline and scenario levels are shown. Paired impact calculations will use the updated Visualiser release.')
        if comparison_set:
            if self.paired:
                # Accumulator memory scales with one baseline/alternative pair,
                # independent of the number of alternatives in the experiment.
                # The first pair writes the common baseline; later pairs only
                # write their alternative. Scientific calculations are unchanged.
                for index in range(1,max(2,len(groups))):
                    metrics=groups[0]+(groups[index] if index<len(groups) else [])
                    outputs=([dict(path=str(series[0]),role='baseline')] if index==1 else [])
                    if index<len(groups):
                        outputs.append(dict(path=str(series[index]),role='scenario_'+str(index)))
                    atomic_json(work/'request.json',dict(operation='aggregate',metrics=metrics,outputs=outputs))
                    command([self.node,'--max-old-space-size='+str(heap),
                             str(self.build/'runner.cjs'),str(work/'request.json')])
            return AggregateFiles(tuple(series),notice,comparison_available=self.paired)
        output=work/'aggregate.json'
        atomic_json(work/'request.json',dict(operation='aggregate',metrics=[m for group in groups for m in group],output=str(output)))
        command([self.node,'--max-old-space-size='+str(heap),
                 str(self.build/'runner.cjs'),str(work/'request.json')])
        return AggregateFiles((output,),notice,comparison_available=self.paired and len(sources)>1)
