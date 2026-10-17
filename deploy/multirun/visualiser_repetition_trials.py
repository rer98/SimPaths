#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Laptop Docker benchmark of retained-summary regeneration for two, five and ten
synthetic repetitions. Fixed CPU/RAM, stage timings, immutable input snapshots.
@author ross richardson
"""
import argparse
import csv
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import shutil
import signal
import statistics
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy.multirun import visualiser_capacity as legacy
from deploy.multirun.visualiser_capacity_comparison import rebind, metrics
from deploy.multirun.visualiser_repetition_worker import NOTICE, STAGES, trial_order
from deploy.multirun.artifacts import fingerprint, snapshot_files, write_attribution
from deploy.multirun.visualiser_capacity_worker import write_json

LIMITATIONS = [NOTICE,
    'One baseline and one alternative; all 52 annual years, with matching synthetic seed labels.',
    'Measures genuine cache restore, statistical combination, publication and chart indexing.',
    'Synthetic relabelling/cache priming and input verification are reported separately, outside trials.',
    'Raw CSV parsing, raw-source verification, scheduler waits, HTTP/network and active simulations are excluded.',
    'Filesystem caches are not forcibly cleared; trial order rotates between rounds.',
    'CPU and RAM are fixed across counts; this does not establish VPS mixed-load performance.',
    'Quarter-second sampled memory peaks can miss spikes; whole-container cgroup peak includes priming.']


def options(args, snapshot, fixture, destination, identity):
    value = legacy.container_options(args.image_id, snapshot, snapshot/'fixture', None,
                                     destination,args.memory_mib,identity)
    value['entrypoint'] = ['python3','/tools/visualiser_repetition_worker.py']
    value['command'] += ['--cpu-millis',str(args.cpu_millis),'--repetitions',
                         *map(str,args.repetitions),'--rounds',str(args.rounds)]
    value['nano_cpus'] = args.cpu_millis*1_000_000
    return value


def check_results(report, counts, rounds):
    cases = report.get('cases',[])
    expected = trial_order(counts,rounds)
    if (report.get('passed') is not True or report.get('cleanup') is not True
            or [(c.get('round'),c.get('repetitions')) for c in cases] != expected
            or any(not c.get('passed') or not c.get('cleanup') for c in cases)):
        raise ValueError('Worker did not complete every repetition case and cleanup')
    for count in counts:
        checksums = {json.dumps(c['result_identity'],sort_keys=True) for c in cases if c['repetitions']==count}
        if len(checksums) != 1:
            raise ValueError('Publication/chart output changed between rounds for '+str(count)+' repetitions')
    return cases


def run_container(client,args,snapshot,fixture,directory):
    directory.mkdir(mode=0o700)
    identity = uuid4().hex
    container = None
    record = dict(passed=False,cleanup=False,preflight=legacy.preflight_space(directory,args.memory_mib,1))
    started = time.monotonic()
    try:
        container = client.containers.create(**options(args,snapshot,fixture,directory,identity))
        record['container_id'] = container.id
        container.start()
        deadline = started+args.timeout_seconds
        shown = 0
        while True:
            container.reload()
            if (directory/'report.json').is_file():
                progress = legacy.read_json(directory/'report.json')
                completed = [c for c in progress.get('cases',[]) if c.get('passed') and c.get('cleanup')]
                for case in completed[shown:]:
                    print(f"PASS: round {case['round']}, {case['repetitions']} repetitions; "
                          f"ready in {case['regeneration_seconds']:.1f} s",flush=True)
                shown = len(completed)
            if not container.attrs['State']['Running']:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('Repetition benchmark exceeded its time bound')
            if shutil.disk_usage(directory).free < legacy.GIB:
                raise RuntimeError('Less than 1 GiB disk headroom; stopped benchmark')
            time.sleep(.5)
        state = container.attrs['State']
        record.update(exit_code=state['ExitCode'],oom_killed=bool(state.get('OOMKilled')))
        if (directory/'report.json').is_file():
            record['worker'] = legacy.read_json(directory/'report.json')
        if record['exit_code'] or record['oom_killed']:
            raise RuntimeError('Worker failed or exhausted container memory')
        record['cases'] = check_results(record.get('worker',{}),args.repetitions,args.rounds)
        record['worker_metrics'] = metrics(record['worker'])
        if record['worker_metrics'].get('oom_events'):
            raise RuntimeError('Worker recorded a memory exhaustion event')
        record['passed'] = True
    finally:
        errors = []
        if container is not None:
            try:
                (directory/'container.log').write_bytes(container.logs()[-2*legacy.MIB:])
            except BaseException as error:
                errors.append('diagnostics:'+type(error).__name__)
            try:
                legacy.cleanup_container(container,identity)
            except BaseException as error:
                errors.append('container:'+type(error).__name__)
        record['cleanup'] = not errors
        if record['cleanup']:
            # Reclaim only generated scratch, after native descendants are gone.
            scratch = directory/'private'
            if scratch.exists() and not scratch.is_symlink():
                try:
                    shutil.rmtree(scratch)
                except BaseException as error:
                    errors.append('scratch:'+type(error).__name__)
        record['cleanup'] = not errors
        record['cleanup_errors'] = errors
        if errors:
            record['passed'] = False
        record['elapsed_seconds'] = time.monotonic()-started
        write_json(directory/'container-result.json',record)
    if not record['cleanup']:
        raise RuntimeError('Container cleanup was not confirmed; inspect private evidence')
    return record


def summaries(cases):
    return [dict(repetitions=count,trials=len(rows),
            regeneration_seconds=statistics.median(c['regeneration_seconds'] for c in rows),
            minimum_seconds=min(c['regeneration_seconds'] for c in rows),
            maximum_seconds=max(c['regeneration_seconds'] for c in rows),
            stages={s:statistics.median(c['stages'][s]['elapsed_seconds'] for c in rows) for s in STAGES},
            publication_bytes=statistics.median(c['publication']['bytes'] for c in rows),
            chart_bytes=statistics.median(c['index']['section_bytes']+c['index']['index_bytes'] for c in rows))
        for count in sorted({c['repetitions'] for c in cases})
        for rows in [[c for c in cases if c['repetitions']==count]]]


def write_tables(directory,report):
    cases = report.get('cases',[])
    columns = ['round','repetitions','regeneration_seconds',*STAGES,'publication_mib','charts_mib']
    with (directory/'trials.csv').open('w',newline='') as target:
        writer = csv.writer(target); writer.writerow(columns)
        for c in cases:
            writer.writerow([c['round'],c['repetitions'],c['regeneration_seconds'],
                *[c['stages'][s]['elapsed_seconds'] for s in STAGES],c['publication']['bytes']/legacy.MIB,
                (c['index']['section_bytes']+c['index']['index_bytes'])/legacy.MIB])
    def row(values,heading=False):
        tag = 'th' if heading else 'td'
        return '<tr>'+''.join(f'<{tag}>{html.escape(str(v))}</{tag}>' for v in values)+'</tr>'
    header = row(['Repetitions per scenario','Rounds','Median time to ready (s)','Range (s)',
        'Restore summaries (s)','Combine statistics (s)','Validate rows (s)',
        'Write publication (s)','Index charts (s)','Catalogue (s)','First chart read (s)',
        'Publication (MiB)','Chart files (MiB)'],True)
    rows = []
    for s in report.get('summary',[]):
        rows.append(row([s['repetitions'],s['trials'],f"{s['regeneration_seconds']:.2f}",
            f"{s['minimum_seconds']:.2f}–{s['maximum_seconds']:.2f}",
            *[f"{s['stages'][name]:.2f}" for name in STAGES],
            f"{s['publication_bytes']/legacy.MIB:.2f}",f"{s['chart_bytes']/legacy.MIB:.2f}"]))
    text = ('<!-- (C) Copyright 2026, by Ross Richardson\n'
        'Generated retained-summary repetition benchmark tables.\n@author ross richardson\n-->\n'
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Visualiser regeneration by repetition count</title>'
        '<style>body{font:16px system-ui;margin:2em;color:#193249}table{border-collapse:collapse;font-size:14px}'
        'th,td{border:1px solid #bdcbd5;padding:.6em;text-align:right}th{background:#e8f0f5}'
        'p,li{max-width:78em}caption{text-align:left;font-weight:bold;margin:1em 0}</style>'
        '<h1>Visualiser regeneration by repetition count</h1>'
        '<p><strong>'+('PASS' if report.get('passed') else 'INCOMPLETE / FAILED')+'</strong> · '
        +html.escape(NOTICE)+'</p><p>One worker; '+str(report['cpu_millis']/1000)+' vCPU; '
        +str(report['memory_mib']/1024)+' GiB container RAM; no swap.</p>'
        '<div style="overflow-x:auto"><table><caption>Seconds to recreate one baseline–alternative publication '
        'from retained summaries. Lower is faster.</caption><thead>'+header+'</thead><tbody>'
        +''.join(rows)+'</tbody></table></div>'
        '<p>Time to ready includes restoration, combination, validation, publication, indexing, catalogue and '
        'intervening pipeline setup. The first chart read occurs afterward. Stage medians need not add up to '
        'the median time to ready. Priming and fixture verification are excluded. CSV lists each individual round.</p>'
        '<ul>'+''.join('<li>'+html.escape(s)+'</li>' for s in report['limitations'])+'</ul></html>\n')
    (directory/'comparison.html').write_text(text)


def run(args):
    import docker
    if os.getuid() == 0:
        raise ValueError('Use your normal Docker-enabled user, not root')
    trial_order(args.repetitions,args.rounds)
    for name in ('frontend','fixture','source_build','build'):
        setattr(args,name,getattr(args,name).resolve(strict=True))
    args.output = args.output.absolute()
    if any(p.is_symlink() for p in (args.output,*args.output.parents)):
        raise ValueError('Output must not use symlinked directories')
    if any(args.output==p or p in args.output.parents for p in
           (args.frontend,args.fixture,args.source_build,args.build)):
        raise ValueError('Keep benchmark evidence outside input directories and repositories')
    args.output.mkdir(mode=0o700,parents=True,exist_ok=False)
    write_attribution(args.output)
    report = dict(format='simpaths.visualiser.repetition-trials.v1',passed=False,cleanup=True,
        model_executions=False,production_changed=False,synthetic=True,raw_parsing=False,
        started_at=datetime.now(timezone.utc).isoformat(),memory_mib=args.memory_mib,
        cpu_millis=args.cpu_millis,repetitions=args.repetitions,rounds=args.rounds,cases=[],limitations=LIMITATIONS)
    client = None
    try:
        with legacy.exclusive(args.output.parent):
            report['preflight'] = legacy.preflight_space(args.output,args.memory_mib,1)
            snapshot = args.output/'snapshot'; snapshot.mkdir(mode=0o700)
            rebind(SimpleNamespace(fixture=args.fixture,source_build=args.source_build,build=args.build,
                                   output=snapshot/'fixture',alternatives=1))
            fixture,_ = legacy.check_fixture(snapshot/'fixture')
            report['sources'] = legacy.snapshot_tools(args.frontend,args.build,snapshot,fixture)
            worker = ROOT/'deploy/multirun/visualiser_repetition_worker.py'
            report['sources']['tools'][worker.name] = fingerprint(worker,snapshot/'tools'/worker.name)
            report['sources']['supervisor'] = snapshot_files({
                'visualiser_repetition_trials.py':Path(__file__),
                'visualiser_capacity_comparison.py':ROOT/'deploy/multirun/visualiser_capacity_comparison.py'},
                snapshot/'supervisor')
            report['fixture'] = fingerprint(snapshot/'fixture'/'manifest.json')
            client = docker.from_env(timeout=30)
            if client.info().get('CgroupVersion') != '2':
                raise ValueError('Native cgroup v2 CPU/RAM limits are required')
            args.image_id = client.images.get(args.image).id
            report['image_id'] = args.image_id
            print('Running retained-summary trials; log: '+str(args.output/'worker/container.log'),flush=True)
            result = run_container(client,args,snapshot,fixture,args.output/'worker')
            report.update(passed=result['passed'],cleanup=result['cleanup'],cases=result.get('cases',[]),
                          container=result)
            legacy.check_fixture(args.fixture)
            legacy.check_fixture(snapshot/'fixture')
            report['summary'] = summaries(report['cases'])
    except (Exception,KeyboardInterrupt) as error:
        report.update(passed=False,error_type=type(error).__name__,error=str(error)[:500])
        # Cleanup evidence is written by the container runner even on failure.
        result_path = args.output/'worker/container-result.json'
        if result_path.is_file():
            report['container'] = legacy.read_json(result_path)
            report['cleanup'] = report['container']['cleanup']
        print('STOPPED: '+type(error).__name__+'; inspect retained evidence.',file=sys.stderr)
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        write_json(args.output/'report.json',report)
        write_tables(args.output,report)
        if client is not None:
            client.close()
    print('Evidence: '+str(args.output/'report.json'),flush=True)
    print('Readable tables: '+str(args.output/'comparison.html'),flush=True)
    return 0 if report['passed'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('frontend','fixture','source-build','build','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--image',default='simpaths-visualiser-capacity:20261015')
    parser.add_argument('--repetitions',type=int,nargs='+',default=[2,5,10])
    parser.add_argument('--rounds',type=int,choices=range(1,6),default=3)
    parser.add_argument('--memory-mib',type=int,choices=(2048,3072,4096),default=3072)
    parser.add_argument('--cpu-millis',type=int,choices=(1000,2000),default=1000)
    parser.add_argument('--timeout-seconds',type=int,default=7200)
    args = parser.parse_args(argv)
    trial_order(args.repetitions,args.rounds)
    if not 60 <= args.timeout_seconds <= 43200:
        parser.error('Use a time bound of 60..43200 seconds')
    os.umask(0o077)
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupted)
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
