#!/usr/bin/env python3
# (C) Copyright 2026, by Ross Richardson
# Native proof of delegated aggregate CPU/RAM limits and descendant cleanup.
# Uses tiny disposable helper processes, no Docker, database or model data.
# @author ross richardson
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading


HELPER = r'''
import json, pathlib, subprocess, sys
relative=next(row[3:] for row in pathlib.Path('/proc/self/cgroup').read_text().splitlines() if row.startswith('0::'))
root=pathlib.Path('/sys/fs/cgroup')/relative.lstrip('/')
data=bytearray(16*1024**2)
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print(json.dumps(dict(cpu=(root/'cpu.max').read_text().strip(),
    memory=(root/'memory.max').read_text().strip(),swap=(root/'memory.swap.max').read_text().strip(),
    group=(root/'memory.oom.group').read_text().strip(),descendant=child.pid)),flush=True)
'''


PARALLEL = r'''
import json, os, pathlib, sys, time
relative=next(row[3:] for row in pathlib.Path('/proc/self/cgroup').read_text().splitlines() if row.startswith('0::'))
root=pathlib.Path('/sys/fs/cgroup')/relative.lstrip('/')
folder=pathlib.Path(sys.argv[1]);os.fstat(int(sys.argv[3]))
(folder/('parallel-'+sys.argv[2]+'.json')).write_text(json.dumps(dict(
    cpu=(root/'cpu.max').read_text().strip(),memory=(root/'memory.max').read_text().strip(),
    swap=(root/'memory.swap.max').read_text().strip(),cgroup=relative)))
deadline=time.monotonic()+10
while len(list(folder.glob('parallel-*.json')))<2:
    if time.monotonic()>deadline:raise RuntimeError('Parallel peer did not enter the group')
    time.sleep(.01)
'''


def execute(output,*,parallel=False):
    from jasmine_web.batch.policy import Resources
    from jasmine_web.batch.processing_scope import DelegatedScopes
    if output.exists():
        raise ValueError('Choose a new evidence directory')
    output.mkdir(parents=True,mode=0o700)
    (output/'COPYRIGHT.md').write_text('''<!-- (C) Copyright 2026, by Ross Richardson
Native helper-limit proof and generated numeric evidence.
@author ross richardson
-->
''')
    record=dict(format='simpaths.processing.scope.proof.v1',passed=False,checks=[])
    try:
        scopes=DelegatedScopes(minimum_memory_mib=7168 if parallel else 2304)
        if parallel:
            from jasmine_web.batch.downloads import lock
            from jasmine_web.batch.preparation_commands import PreparationCommands
            with lock(output/'lifecycle.lock') as descriptor,scopes(Resources(2000,5120,512),'proof-parallel') as scope:
                with PreparationCommands(output,timeout='/usr/bin/timeout',runtime=15,check=lambda:None,
                        stop=threading.Event(),pass_fds=(descriptor,),scope=scope,workers=2) as commands:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        tasks=[pool.submit(commands,[sys.executable,'-c',PARALLEL,str(output),str(i),str(descriptor)]) for i in range(2)]
                        for task in tasks:task.result(timeout=20)
                    if commands.children:raise AssertionError('Parallel commands did not join')
                values=[json.loads((output/('parallel-'+str(i)+'.json')).read_text()) for i in range(2)]
                if (any(v['cpu']!='200000 100000' or v['memory']!=str(5120*1024**2) or v['swap']!='0' for v in values)
                        or values[0]['cgroup']!=values[1]['cgroup']):
                    raise AssertionError('Parallel commands did not share the admitted group')
            record['checks'].append('two production commands share one 5 GiB/2-vCPU group, inherit lifecycle guards and join before release')
        with scopes(Resources(1500,128,512),'proof-small') as scope:
            result=subprocess.run(scope.wrap([sys.executable,'-c',HELPER]),pass_fds=scope.pass_fds,
                env={'PATH':os.defpath,'LANG':'C.UTF-8'},stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
            if result.returncode or len(result.stdout)>4096:
                raise AssertionError('Bounded helper failed')
            values=json.loads(result.stdout)
            if (values['cpu']!='150000 100000' or values['memory']!=str(128*1024**2)
                    or values['swap']!='0' or values['group']!='1'):
                raise AssertionError('Kernel helper limits differ')
            record['checks'].append('fractional CPU, whole-group memory, no swap and group OOM are enforced')
        process=Path('/proc')/str(values['descendant'])/'stat'
        if process.exists() and process.read_text().split(') ',1)[1].split()[0] not in ('Z','X'):
            raise AssertionError('A descendant survived verified scope cleanup')
        record['checks'].append('a descendant that closed inherited descriptors is stopped before cleanup completes')
        with scopes(Resources(1000,128,512),'proof-oom') as scope:
            result=subprocess.run(scope.wrap([sys.executable,'-c','data=bytearray(256*1024**2)']),
                pass_fds=scope.pass_fds,env={'PATH':os.defpath,'LANG':'C.UTF-8'},
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
            events=dict(line.split() for line in (scopes._path('proof-oom')/'memory.events').read_text().splitlines())
            if result.returncode==0 or int(events.get('oom_kill',0))<1:
                raise AssertionError('The small helper did not hit its enforced group limit')
        record['checks'].append('an oversized helper is OOM-killed within its own group; the service survives')
        if any(scopes._path(name).exists() for name in ('proof-small','proof-oom','proof-parallel')):
            raise AssertionError('Empty helper groups were not removed')
        record['passed']=True
    except BaseException as error:
        record['error_type']=type(error).__name__
        raise
    finally:
        (output/'report.json').write_text(json.dumps(record,indent=2)+'\n')
        print(('PASSED: ' if record['passed'] else 'FAILED: ')+str(output/'report.json'),flush=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--parallel-seeds',action='store_true',help='Also check two production commands in one 5 GiB/2-vCPU group; enclosing MemoryMax >= 7G')
    args=parser.parse_args(argv)
    sys.path.insert(0,str(args.frontend.resolve(strict=True)))
    execute(args.output.resolve(),parallel=args.parallel_seeds)
    return 0


if __name__=='__main__':raise SystemExit(main())
