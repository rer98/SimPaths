"""(C) Copyright 2026, by Ross Richardson

Pinned Visualiser adapter: private VM aggregation, approved vocabulary and charts.
Own-input/public previews use upstream seed-paired multi-scenario calculations;
restricted provider results still require approved disclosure controls.
@author ross richardson
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


class VisualiserBackend:
    # Explicit public resources from the trusted application, never a public-dir
    # glob that could also expose bundled simulation data or server calculations.
    APPLICATION_ASSETS=frozenset({'index.html','visualiser.js','visualiser.css',
        'pmh_logo.png','UKRILogo.png','Interpreting-results.html','Interpreting-results.css',
        'citation.html','citation.css','SimPaths-logo-transparent.png'})

    def __init__(self, build, execution_root, *, public_datasets=(), memory_mib=1024):
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
        self.paired=manifest['mode']=='development-paired'
        # Advertise a set only when the verified application bundle includes
        # its v2 reader. Older pinned builds still support the original pair.
        self.supports_selective_views=b'simpaths.visualiser.catalogue.v1' in self.assets['visualiser.js']
        self.supports_comparison_sets=self.supports_selective_views or b'simpaths.visualiser.v2' in self.assets['visualiser.js']
        self.public_datasets=frozenset(public_datasets)
        self.identity=dict(build=manifest,publication='own-and-public-preview-v1',
                           public_datasets=sorted(self.public_datasets),node=version,memory_mib=memory_mib)

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

    def process(self,sources,work,command,progress,*,comparison_set=False):
        """Dictionary compatibility for calculation tests and trusted tooling."""
        from jasmine_web.batch.visualiser import artifact
        files=self.process_files(sources,work,command,progress,comparison_set=comparison_set)
        result=dict(comparison_available=files.comparison_available,notice=files.notice)
        if comparison_set:
            result['series']=[dict(configuration=c['configuration']['id'],rows=artifact(path))
                              for c,path in zip(sources,files.paths)]
        else:
            result['rows']=artifact(files.paths[0])
        return result

    def process_files(self,sources,work,command,progress,*,comparison_set=False):
        """Leave bounded JSON rows private for the platform's aggregate helper."""
        from jasmine_web.batch.aggregate_io import AggregateFiles
        from jasmine_web.batch.local_executor import atomic_json
        from jasmine_web.batch.results import verified_file, OutputUnavailable
        if comparison_set and not self.supports_comparison_sets:
            raise ValueError('This Visualiser build supports single/pair results only')
        self._verify_code()
        metrics=[]
        series=[]
        done=0
        for configuration_index,configuration in enumerate(sources):
            config=configuration['configuration']
            role=('baseline' if configuration_index==0 else 'scenario_'+str(configuration_index)) if comparison_set and self.paired else config['role'].lower()
            configuration_metrics=[]
            for index,run in enumerate(config['runs']):
                selected=[f for f in configuration['files']
                          if f['name'].startswith(run['folder']+'/csv/')]
                def one(kind):
                    files=[f for f in selected if kind in Path(f['name']).name.lower()]
                    if len(files)!=1:
                        raise ValueError('Need one person and benefit file per run')
                    return files[0]
                inputs=[one('person'),one('benefit')]
                output=work/('configuration-'+str(configuration_index)+'-run-'+str(index)+'.json')
                with ExitStack() as stack:
                    descriptors=[stack.enter_context(verified_file(self.root,item)) for item in inputs]
                    before=[os.fstat(f.fileno()) for f in descriptors]
                    atomic_json(work/'request.json',dict(operation='run',
                        person_fd=descriptors[0].fileno(),benefit_fd=descriptors[1].fileno(),
                        role=role,run=run['seed'],output=str(output)))
                    command([self.node,'--max-old-space-size='+str(max(128,self.memory-256)),
                             str(self.build/'runner.cjs'),str(work/'request.json')],
                            pass_fds=tuple(f.fileno() for f in descriptors))
                    for initial,source in zip(before,descriptors):
                        after=os.fstat(source.fileno())
                        if (initial.st_size,initial.st_mtime_ns,initial.st_ctime_ns)!=(
                                after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                            raise OutputUnavailable()
                configuration_metrics.append(str(output));done+=1;progress(done)
            if comparison_set:
                output=work/('configuration-'+str(configuration_index)+'-aggregate.json')
                if self.paired:
                    metrics.extend(configuration_metrics)
                else:
                    # Retained older builds aggregate one alternative at a time.
                    atomic_json(work/'request.json',dict(operation='aggregate',metrics=configuration_metrics,output=str(output)))
                    command([self.node,'--max-old-space-size='+str(max(128,self.memory-256)),
                             str(self.build/'runner.cjs'),str(work/'request.json')])
                series.append(output)
            else:
                metrics.extend(configuration_metrics)
        notice='Development preview using Visualiser revision '+self.manifest['revision'][:12]+(
            '. Policy impacts and uncertainty use runs with matching random seeds.' if self.paired else
            '. Baseline and scenario levels are shown. Paired impact calculations will use the updated Visualiser release.')
        if comparison_set:
            if self.paired:
                atomic_json(work/'request.json',dict(operation='aggregate',metrics=metrics,
                    outputs=[dict(path=str(path),role='baseline' if index==0 else 'scenario_'+str(index))
                             for index,path in enumerate(series)]))
                command([self.node,'--max-old-space-size='+str(max(128,self.memory-256)),
                         str(self.build/'runner.cjs'),str(work/'request.json')])
            return AggregateFiles(tuple(series),notice,comparison_available=self.paired)
        output=work/'aggregate.json'
        atomic_json(work/'request.json',dict(operation='aggregate',metrics=metrics,output=str(output)))
        command([self.node,'--max-old-space-size='+str(max(128,self.memory-256)),
                 str(self.build/'runner.cjs'),str(work/'request.json')])
        return AggregateFiles((output,),notice,comparison_available=self.paired and len(sources)>1)
