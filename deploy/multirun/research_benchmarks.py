#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Compare fixed-resource research trials and archive audited evidence before disposal.
Maintainer-only, standard-library tooling; never launches models or reads progress.
@author ross richardson
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from deploy.multirun.artifacts import ArtifactError, fingerprint, relative_name, write_attribution

MIB = 1024**2
GIB = 1024**3
MAX_JSON_BYTES = 4*MIB
ARCHIVE_FORMAT = 'simpaths.research-benchmark-archive.v1'
REPORT_FORMAT = 'simpaths.research-benchmark-comparison.v1'
DISPOSAL_FORMAT = 'simpaths.research-benchmark-disposal.v1'
EVIDENCE = ('report.json', 'model-proof/report.json', 'model-proof/configuration.json',
            'model-proof/output-manifest.json')
SETTINGS = {'population', 'end_year', 'repetitions', 'cpu_millis', 'heap_mib', 'memory_mib',
            'max_memory_mib', 'storage_mib', 'setup_seconds', 'repetition_seconds', 'max_attempts'}
CASE_LIMITS = {'A': (4096, 5120), 'B': (4096, 6144), 'C': (5120, 6144),
               'D': (4096, 5120), 'E': (4096, 5120)}
CASE_CPUS = {'A': 2000, 'B': 2000, 'C': 2000, 'D': 1500, 'E': 1000}
CASE_FAMILIES = {'memory': ('A', 'B', 'C'), 'cpu': ('A', 'D', 'E'), 'all': tuple(CASE_LIMITS)}
ANNUAL_FILES = frozenset(('AlignmentStatistics.csv', 'BenefitUnit.csv',
    'DemographicStatistics.csv', 'HealthStatistics.csv', 'Household.csv',
    'LabourStatistics.csv', 'Person.csv', 'WealthIncomeStatistics.csv', 'WellbeingByGender.csv'))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def checksum(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def instant():
    return datetime.now(timezone.utc).isoformat()


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ArtifactError('Expected a non-negative integer measurement')
    return value


def number(value, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ArtifactError('Expected a finite numeric measurement')
    return value


def ordinary(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ArtifactError('Symlinks are not permitted in benchmark evidence')
    return path


def read_json(path):
    path = ordinary(path)
    with parent_fd(path) as (parent,name):
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_JSON_BYTES:
                raise ArtifactError('Expected an ordinary benchmark JSON file at most 4 MiB')
            data=stream.read(MAX_JSON_BYTES+1)
            after=os.fstat(stream.fileno());current=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if (len(data)>MAX_JSON_BYTES or state_tuple(before)!=state_tuple(after)
                    or state_tuple(before)!=state_tuple(current)):
                raise ArtifactError('Benchmark JSON changed while being read')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:raise ArtifactError('Duplicate benchmark JSON key')
            result[key] = value
        return result
    def invalid(_):raise ArtifactError('Non-finite benchmark JSON')
    value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    return value, {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}


def write_json(path, value, *, replace=False):
    path = ordinary(path)
    data = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+'\n').encode()
    if len(data) > MAX_JSON_BYTES:raise ArtifactError('Generated benchmark JSON exceeds 4 MiB')
    if replace:
        fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.new',dir=path.parent)
        temporary=Path(name)
    else:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        temporary=path
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        if replace:os.replace(temporary,path)
    finally:
        if replace and temporary.exists():temporary.unlink()
    fd = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def check_manifest(manifest):
    if type(manifest) is not dict or not 1 <= len(manifest) <= 1000:
        raise ArtifactError('Expected a bounded original output manifest')
    for name, value in manifest.items():
        relative_name(name)
        if (type(value) is not dict or set(value) != {'bytes', 'sha256'}
                or type(value['sha256']) is not str
                or not re.fullmatch('[a-f0-9]{64}', value['sha256'])):
            raise ArtifactError('Invalid original output fingerprint')
        integer(value['bytes'])


def validate_completed(evidence):
    outer = evidence['report.json']['value']
    record = evidence['model-proof/report.json']['value']
    config = evidence['model-proof/configuration.json']['value']
    manifest = evidence['model-proof/output-manifest.json']['value']
    if any(type(value) is not dict for value in (outer,record,config,manifest)):
        raise ArtifactError('Completion metadata must contain JSON objects')
    if (outer.get('passed') is not True or outer.get('cleanup') is not True
            or any(record.get(k) is not True for k in ('passed', 'cleanup', 'source_unchanged',
                'input_copies_reclaimed', 'reservations_released')) or record.get('browser_errors')):
        raise ArtifactError('Use successful trials with confirmed termination and cleanup')
    settings = record['settings']
    if type(settings) is not dict or set(settings) not in (SETTINGS, SETTINGS-{'cpu_millis'}):
        raise ArtifactError('Incomplete frozen benchmark settings')
    for key, value in settings.items():integer(value)
    cpu_millis = settings.get('cpu_millis', 2000)
    if (not 1000 <= settings['population'] <= 100000 or not 2019 <= settings['end_year'] <= 2070
            or not 1000 <= cpu_millis <= 2000
            or not 1024 <= settings['heap_mib'] <= 7168
            or not 5120 <= settings['memory_mib'] <= 8192
            or not 6144 <= settings['storage_mib'] <= 65536
            or not 0 <= settings['setup_seconds'] <= 3600
            or not 60 <= settings['repetition_seconds'] <= 86400):
        raise ArtifactError('Settings exceed the bounded private calibration workload')
    if (settings['max_attempts'] != 1 or settings['memory_mib'] != settings['max_memory_mib']
            or settings['heap_mib']+1024 > settings['memory_mib']
            or not 1 <= settings['repetitions'] <= 10):
        raise ArtifactError('Fixed benchmarks require one attempt and no resource growth')
    for event in record['resource_events']:
        if (event.get('action')!='waiting' or event.get('attempt')!=1
                or event.get('kind') not in ('memory','storage')
                or event.get('allocation')!=dict(cpu_millis=cpu_millis,memory_mib=settings['memory_mib'],
                                               storage_mib=settings['storage_mib'])):
            raise ArtifactError('Fixed benchmarks cannot include confirmed growth or resource retries')
    attempts = record['attempts']
    if len(attempts) != 1 or attempts[0]['outcome'] != 'success':
        raise ArtifactError('Expected exactly one successful fixed-memory attempt')
    attempt = attempts[0]
    if 'cpu_millis' in settings and (type(attempt.get('cpu_millis')) is not int
            or attempt['cpu_millis'] != cpu_millis
            or type(attempt.get('jvm_active_processor_count')) is not int
            or attempt['jvm_active_processor_count'] != 2
            or type(attempt.get('runner_sha256')) is not str
            or not re.fullmatch('[a-f0-9]{64}', attempt['runner_sha256'])):
        raise ArtifactError('Observed Docker quota or fixed JVM launch setting differs from the trial')
    if (attempt['heap_mib'] != settings['heap_mib']
            or attempt['observed_container_limits_bytes'] != [settings['memory_mib']*MIB]
            or attempt['peak']['heap_max_bytes'] != settings['heap_mib']*MIB):
        raise ArtifactError('Observed ceilings differ from the fixed settings')
    seeds = config['seed_plan']['seeds']
    common = config['common']
    if (common['country']!='UK' or common['start_year']!=2019
            or common['population'] != settings['population'] or common['end_year'] != settings['end_year']
            or common['start_year'] != record['start_year']
            or len(config['run_sets']) != 1 or len(seeds) != settings['repetitions']
            or config['seed_plan']['repetitions'] != settings['repetitions']
            or len(set(seeds)) != len(seeds) or any(not re.fullmatch('[0-9]+', s) for s in seeds)
            or [v['seed'] for v in record['verified_repetitions']] != seeds
            or record['annual_years'] != list(range(common['start_year'], settings['end_year']+1))):
        raise ArtifactError('Frozen population, years or original seeds differ')
    check_manifest(manifest)
    for key in ('prepared_sha256',):
        if type(record.get(key)) is not str or not re.fullmatch('[a-f0-9]{64}',record[key]):
            raise ArtifactError('Missing immutable input or calibration-driver identity')
    sources=record.get('source_sha256')
    if (type(sources) is not dict or set(sources)!={'deploy/multirun/research_calibration.py',
                                                               'deploy/acceptance/SimPathsResourceProbe.java'}
            or any(type(v) is not str or not re.fullmatch('[a-f0-9]{64}',v) for v in sources.values())):
        raise ArtifactError('Missing calibration-driver or JVM monitor source identity')
    if type(record.get('image')) is not str or not re.fullmatch('sha256:[a-f0-9]{64}',record['image']):
        raise ArtifactError('Missing immutable runtime image identity')
    check_manifest({'model.jar':record['model']});check_manifest(record['probe_classes'])
    if sum(v['bytes'] for v in manifest.values()) != record['output_bytes']:
        raise ArtifactError('Original output size differs from the recorded manifest')
    runs = {}
    for name, value in manifest.items():
        parts = name.split('/')
        match = re.fullmatch(r'.+_([0-9]+)_([0-9]+)', parts[0])
        if (not match or match[1] not in seeds or int(match[2]) != seeds.index(match[1])
                or len(parts) not in (2, 3)):
            raise ArtifactError('Output folders do not identify the original seed/ordinal')
        seed = match[1]
        run = runs.setdefault(seed, {'folder':parts[0], 'annual':{}})
        if run['folder'] != parts[0]:raise ArtifactError('Multiple folders for the same seed')
        if len(parts) == 3 and parts[1] == 'csv' and parts[2] in ANNUAL_FILES:
            run['annual'][parts[2]] = value
        if len(parts) == 3 and parts[1] == 'input' and parts[2] != 'options.txt':
            raise ArtifactError('Large input copies have not been reclaimed')
    if set(runs) != set(seeds) or any(set(r['annual']) != ANNUAL_FILES for r in runs.values()):
        raise ArtifactError('Missing annual CSVs for an original seed')
    for row in record['status_requests']:
        integer(row['status'],100);number(row['ms'])
    number(attempt['container_lifetime_seconds'], 0.001)
    if len(attempt['model_initialisation_seconds'])!=settings['repetitions']:
        raise ArtifactError('Missing model initialisation timers for the original repetitions')
    for value in attempt['model_initialisation_seconds']:number(value,0.001)
    integer(attempt['elapsed_ms'],1);integer(attempt['gc_ms']);integer(attempt['samples'],1)
    for value in attempt['peak'].values():integer(value)
    integer(record['peak_workspace_bytes']);number(record['elapsed_seconds'])
    integer(record['sample_interval_ms'],1)
    return record, config, manifest, runs


def completion(root):
    root = ordinary(root)
    evidence = {}
    for name in EVIDENCE:
        value, digest = read_json(root/name)
        evidence[name] = {'value':value, 'fingerprint':digest}
    values = validate_completed(evidence)
    if values[0]['output_location'] != str(root/'model-proof/retained-output'):
        raise ArtifactError('Output location differs from the selected completion directory')
    if values[0].get('retained_workspace') and Path(values[0]['retained_workspace']).exists():
        raise ArtifactError('Original scratch cleanup remains unconfirmed')
    return evidence, values


def percentile(values, fraction):
    """Nearest-rank percentile; explicit sample count accompanies each result."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered)*fraction)-1)] if ordered else None


def measurements(record):
    attempt = record['attempts'][0]
    success = [r['ms'] for r in record['status_requests'] if r['status'] == 200]
    return dict(container_seconds=attempt['container_lifetime_seconds'],
        cpu_millis=record['settings'].get('cpu_millis', 2000),
        jvm_active_processor_count=attempt.get('jvm_active_processor_count', 2),
        last_sampled_jvm_seconds=attempt['elapsed_ms']/1000, gc_seconds=attempt['gc_ms']/1000,
        gc_fraction_percent=100*attempt['gc_ms']/attempt['elapsed_ms'],
        proof_seconds=record['elapsed_seconds'], peaks=attempt['peak'],
        model_initialisation_seconds=attempt['model_initialisation_seconds'],
        workspace_bytes=record['peak_workspace_bytes'], retained_bytes=record['output_bytes'],
        samples=attempt['samples'], sample_interval_ms=record['sample_interval_ms'],
        unchanged_allocation_pressure_notices=len(record['resource_events']),
        status=dict(count=len(record['status_requests']), success_count=len(success),
            unexpected_count=len(record['status_requests'])-len(success),
            median_ms=percentile(success,0.5), p95_ms=percentile(success,0.95),
            p99_ms=percentile(success,0.99), maximum_ms=max(success) if success else None,
            percentile_method='nearest-rank; successful status responses only'))


def read_archive(path):
    path = ordinary(path)
    value, digest = read_json(path/'archive.json' if path.is_dir() else path)
    if type(value) is not dict or value.get('format') != ARCHIVE_FORMAT:
        raise ArtifactError('Unsupported benchmark archive')
    for item in value['evidence'].values():
        # The original byte fingerprints bind disposal to originals. Embedded
        # values have separate canonical hashes because JSON formatting differs.
        if checksum(item['value']) != item['value_sha256']:
            raise ArtifactError('Embedded archived evidence changed')
    values = validate_completed(value['evidence'])
    if checksum(value['audit']) != value['audit_sha256']:
        raise ArtifactError('Archived output accounting changed')
    validate_audit(value['source_root'], value['evidence'], value['audit'])
    return value, digest, values


def validate_audit(root, evidence, source):
    record, config, manifest, _ = validate_completed(evidence)
    expected = {Path(name).name:evidence[name]['fingerprint'] for name in EVIDENCE[1:]}
    if (source['folder'] != str(root) or source['provenance'] != expected
            or source['outer_report'] != evidence['report.json']['fingerprint']
            or source['configuration'] != config or source['settings'] != record['settings']
            or source['attempts'] != record['attempts'] or source['bytes'] != record['output_bytes']
            or set(source['files']) != set(manifest)):
        raise ArtifactError('Audit does not match this original completed trial')
    for name, value in source['files'].items():
        if any(value.get(k) != v for k, v in manifest[name].items()):
            raise ArtifactError('Audited output hash or size differs from the original manifest')
        if name.split('/')[-1] in ANNUAL_FILES:
            if (set(value['years']) != {str(y) for y in record['annual_years']}
                    or any(type(y['rows']) is not int or y['rows'] < 1 for y in value['years'].values())):
                raise ArtifactError('Audit lacks annual counts for each original year')
            integer(value['header_bytes'],1);integer(value['rows'],1)
            for year in value['years'].values():integer(year['bytes'],1)
            if (sum(y['rows'] for y in value['years'].values())!=value['rows']
                    or value['header_bytes']+sum(y['bytes'] for y in value['years'].values())!=value['bytes']):
                raise ArtifactError('Audited annual rows/bytes do not reconcile with the file')


def archive(completed, audit_path, output):
    completed = ordinary(completed);output = ordinary(output)
    if output.exists() or output == completed or completed in output.parents or ROOT in output.parents:
        raise ArtifactError('Choose a new archive outside source and completion directories')
    evidence, _ = completion(completed)
    audited, audit_fp = read_json(audit_path)
    if (type(audited) is not dict or audited.get('format') != 'simpaths.private-research-storage-audit.v1'
            or any(audited.get(k) is not True for k in ('passed','read_only','original_manifest_hashes_verified'))
            or audited.get('simulations_started') != 0 or audited.get('microdata_copied') is not False):
        raise ArtifactError('Use the successful read-only storage audit')
    sources = [s for s in audited['sources'] if s['folder'] == str(completed)]
    if len(sources) != 1:raise ArtifactError('Audit must identify this completion once')
    validate_audit(completed, evidence, sources[0])
    for item in evidence.values():item['value_sha256'] = checksum(item['value'])
    result = dict(format=ARCHIVE_FORMAT, created_at=instant(), source_root=str(completed),
        evidence=evidence, audit=sources[0], audit_sha256=checksum(sources[0]), audit_fingerprint=audit_fp,
        simulations_started=0, microdata_copied=False,
        note='Historical completed reports and independently audited file/annual statistics. '
             'No fresh bulk read here; disposal rechecks every remaining original file.')
    output.mkdir(mode=0o700,parents=True)
    write_attribution(output);write_json(output/'archive.json',result)
    return result


def load_case(label, path):
    if path is None:return {'state':'pending'}
    path = ordinary(path)
    if not path.exists():return {'state':'pending', 'source':str(path)}
    if path.is_file() or (path/'archive.json').exists():
        _, _, values = read_archive(path)
        origin = 'archived audited evidence'
    else:
        if not all((path/name).is_file() for name in EVIDENCE):
            return {'state':'incomplete', 'source':str(path)}
        outer,_=read_json(path/'report.json');native,_=read_json(path/'model-proof/report.json')
        if type(outer) is not dict or type(native) is not dict:
            raise ArtifactError('Completion reports must be JSON objects')
        if any(d.get(k) is not True for d in (outer,native) for k in ('passed','cleanup')):
            return {'state':'failed-or-unclean','source':str(path)}
        evidence, values = completion(path)
        origin = 'historical completion metadata; bulk files not reread'
    record, config, manifest, runs = values
    heap, memory = CASE_LIMITS[label]
    if ((record['settings']['heap_mib'],record['settings']['memory_mib']) != (heap,memory)
            or record['settings'].get('cpu_millis', 2000) != CASE_CPUS[label]):
        raise ArtifactError('Case '+label+' does not have its agreed CPU/heap/container ceilings')
    return dict(state='complete',source=str(path), evidence_kind=origin, record=record,
        configuration=config, manifest=manifest, runs=runs, measurements=measurements(record))


def comparison(cases, *, family=None):
    if family is None:
        family = ('cpu' if set(cases) <= set(CASE_FAMILIES['cpu']) else 'all') if set(cases)&{'D','E'} else 'memory'
    labels = CASE_FAMILIES[family]
    if set(cases)-set(labels):raise ArtifactError('Cases do not belong to the selected comparison family')
    loaded = {label:load_case(label,cases.get(label)) for label in labels}
    completed = [label for label,case in loaded.items() if case['state']=='complete']
    comparisons = []
    for left_index,left in enumerate(completed):
        for right in completed[left_index+1:]:
            a,b = loaded[left],loaded[right]
            unchanged = SETTINGS-{'cpu_millis','heap_mib','memory_mib','max_memory_mib'}
            if (a['configuration'] != b['configuration']
                    or any(a['record']['settings'][k] != b['record']['settings'][k] for k in unchanged)
                    or any(a['record'].get(k) != b['record'].get(k) for k in
                        ('image','model','prepared_sha256','source_sha256','probe_classes','sample_interval_ms','enforcement'))):
                raise ArtifactError('Cases '+left+'/'+right+' differ beyond the intended CPU/memory settings')
            if (a['record']['attempts'][0].get('runner_sha256') != b['record']['attempts'][0].get('runner_sha256')
                    or a['record']['attempts'][0].get('jvm_active_processor_count', 2)
                    != b['record']['attempts'][0].get('jvm_active_processor_count', 2)):
                raise ArtifactError('Cases must use the same staged runner and JVM processor-count setting')
            files = []
            for seed in a['configuration']['seed_plan']['seeds']:
                for name in sorted(ANNUAL_FILES):
                    first,second=a['runs'][seed]['annual'][name],b['runs'][seed]['annual'][name]
                    state = ('equal' if first == second else
                             'reproducibility-warning' if name=='BenefitUnit.csv' and first['bytes']==second['bytes']
                             else 'unexpected-mismatch')
                    files.append(dict(seed=seed,name=name,state=state,left=first,right=second))
            first,second=a['measurements']['container_seconds'],b['measurements']['container_seconds']
            comparisons.append(dict(left=left,right=right, controlled_identity_match=True,
                container_seconds_change_percent=100*(second-first)/first,
                annual_files=files,unexpected_mismatches=sum(f['state']=='unexpected-mismatch' for f in files),
                reproducibility_warnings=sum(f['state']=='reproducibility-warning' for f in files),
                note='Single-observation timing difference, not a causal performance estimate. '
                     'Equal-size BenefitUnit differences remain unresolved warnings, not scientific equivalence; '
                     'size changes are unexpected mismatches.'))
    public = {label:{k:v for k,v in case.items() if k not in ('record','manifest','runs')}
              for label,case in loaded.items()}
    return dict(format=REPORT_FORMAT,created_at=instant(),simulations_started=0,microdata_read=False,
        cases=public,comparisons=comparisons,family=family,
        complete=len(completed)==len(labels),
        notes=['Percentiles use nearest rank and report their sample count.',
               'Last sampled JVM elapsed time is not the complete simulation-phase duration.',
               'Recorded peaks are sampled, except the separately labelled cgroup kernel peak counter.',
               'Complete metadata cannot prove that bulk files still exist or still match: use the audit/disposal checks.'])


def render(report):
    def metric(case,key,default='Pending'):
        value=case.get('measurements',{}).get(key)
        return default if value is None else value
    cases=report['cases']
    labels = list(cases)
    headings = [f'{label}: {CASE_CPUS[label]/1000:g} vCPUs / {CASE_LIMITS[label][0]/1024:g} GiB heap / '
                f'{CASE_LIMITS[label][1]/1024:g} GiB container' for label in labels]
    lines=['# Fixed-resource research benchmark comparison','',
           'Generated from completed evidence; unfinished cases remain pending.','',
           '| Measurement | '+' | '.join(headings)+' |',
           '| '+' | '.join(['---']*(len(labels)+1))+' |']
    rows=[('Status',lambda c:c['state']),
          ('Docker CPU quota (vCPUs)',lambda c:metric(c,'cpu_millis')/1000),
          ('Java processor-count setting',lambda c:metric(c,'jvm_active_processor_count')),
          ('Container lifetime (seconds)',lambda c:metric(c,'container_seconds')),
          ('Last sampled JVM elapsed (seconds)',lambda c:metric(c,'last_sampled_jvm_seconds')),
          ('GC counters (seconds)',lambda c:metric(c,'gc_seconds')),
          ('Model initialisation timers (seconds)',lambda c:metric(c,'model_initialisation_seconds')),
          ('GC / last JVM sample (%)',lambda c:metric(c,'gc_fraction_percent')),
          ('Sampled working RAM (GiB)',lambda c:c['measurements']['peaks']['working_set_bytes']/GIB),
          ('Sampled heap usage (GiB)',lambda c:c['measurements']['peaks']['heap_used_bytes']/GIB),
          ('Sampled total container memory (GiB)',lambda c:c['measurements']['peaks']['container_used_bytes']/GIB),
          ('Kernel cgroup peak counter (GiB)',lambda c:c['measurements']['peaks']['cgroup_peak_bytes']/GIB
           if c['measurements']['peaks']['cgroup_peak_bytes'] else 'Unavailable'),
          ('Sampled workspace / retained output (GiB)',lambda c:f"{metric(c,'workspace_bytes')/GIB:.3f} / {metric(c,'retained_bytes')/GIB:.3f}"),
          ('Status requests / unexpected responses',lambda c:f"{c['measurements']['status']['count']} / {c['measurements']['status']['unexpected_count']}"),
          ('Successful status p95 / p99 (ms)',lambda c:f"{c['measurements']['status']['p95_ms']} / {c['measurements']['status']['p99_ms']}"),
          ('Telemetry samples / interval (ms)',lambda c:f"{metric(c,'samples')} / {metric(c,'sample_interval_ms')}"),
          ('Pressure notices with unchanged allocation',lambda c:metric(c,'unchanged_allocation_pressure_notices')),
          ('Whole proof (seconds)',lambda c:metric(c,'proof_seconds'))]
    for title,function in rows:
        values=[]
        for label in labels:
            case=cases[label]
            v=function(case) if case['state']=='complete' or title=='Status' else 'Pending'
            values.append(f'{v:.3f}' if type(v) is float else str(v))
        lines.append('| '+title+' | '+' | '.join(values)+' |')
    lines+=['','## Same-seed annual output comparisons','']
    for pair in report['comparisons']:
        lines.append(f"- {pair['left']}/{pair['right']}: {pair['unexpected_mismatches']} unexpected mismatches; "
                     f"{pair['reproducibility_warnings']} unresolved BenefitUnit warnings. "
                     f"Container duration changed {pair['container_seconds_change_percent']:+.2f}% in these observations.")
        for file in pair['annual_files']:
            if file['state']!='equal':
                lines.append(f"  - Seed {file['seed']}, {file['name']}: {file['state']}; "
                             f"{file['left']['bytes']} / {file['right']['bytes']} bytes. Exact hashes are in report.json.")
    if not report['comparisons']:lines.append('Two completed comparable cases are needed for a pairwise comparison.')
    lines+=['','A single timing per setting does not establish a dependable speed benefit. '
            'BenefitUnit warnings do not certify scientific equivalence.','']
    return '\n'.join(lines)


@contextmanager
def parent_fd(path):
    """Open every directory component without following a substituted symlink."""
    path=Path(path).absolute()
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child
        yield fd,path.name
    finally:os.close(fd)


def file_state(path):
    with parent_fd(path) as (fd,name):value=os.stat(name,dir_fd=fd,follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode):raise ArtifactError('Disposal expects ordinary output files')
    return state_tuple(value)


def state_tuple(value):
    return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


def verified_file(path,expected):
    with parent_fd(path) as (parent,name):
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size!=expected['bytes']:
                raise ArtifactError('Remaining output type or size changed')
            hasher=hashlib.sha256()
            while block:=stream.read(MIB):hasher.update(block)
            after=os.fstat(stream.fileno());current=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if (hasher.hexdigest()!=expected['sha256'] or state_tuple(before)!=state_tuple(after)
                    or state_tuple(before)!=state_tuple(current)):
                raise ArtifactError('Remaining output changed after the original audit')
            return state_tuple(before)


def output_files(root):
    root=ordinary(root)
    if not root.is_dir():raise ArtifactError('Original retained-output directory is unavailable')
    names=set()
    for parent,dirs,files in os.walk(root,followlinks=False):
        for name in dirs:
            if (Path(parent)/name).is_symlink():raise ArtifactError('Symlink in retained output directory')
        for name in files:
            path=Path(parent)/name;relative=path.relative_to(root).as_posix()
            relative_name(relative);file_state(path);names.add(relative)
            if len(names)>1000:raise ArtifactError('Output inventory exceeds the bounded original layout')
    return names


def dispose(archive_path, completed):
    """Explicit, restartable disposal; never deletes reports, inputs or directories."""
    archive_path=ordinary(archive_path)
    archive_file=archive_path/'archive.json' if archive_path.is_dir() else archive_path
    archived,archive_fp,(_,_,manifest,_)=read_archive(archive_file)
    completed=ordinary(completed)
    output=completed/'model-proof/retained-output'
    if archived['source_root']!=str(completed) or completed in archive_file.parents:
        raise ArtifactError('Select the original completion and an archive stored outside it')
    evidence,_=completion(completed)
    for name,item in evidence.items():
        if item['fingerprint']!=archived['evidence'][name]['fingerprint']:
            raise ArtifactError('Original completion metadata changed after archival')
    lock=completed/'model-proof/benchmark-disposal.lock'
    with parent_fd(lock) as (parent,name):
        fd=os.open(name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=parent)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ArtifactError('Invalid disposal lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        journal_path=completed/'model-proof/benchmark-disposal.json'
        receipt={'format':DISPOSAL_FORMAT,'created_at':instant(),'archive':archive_fp,
                 'manifest_sha256':checksum(manifest),'removed':[],'pending':None,'passed':False,
                 'simulations_started':0,'microdata_copied':False}
        if journal_path.exists():
            receipt,_=read_json(journal_path)
            if (receipt.get('format')!=DISPOSAL_FORMAT or receipt.get('archive')!=archive_fp
                    or receipt.get('manifest_sha256')!=checksum(manifest)
                    or len(set(receipt['removed']))!=len(receipt['removed'])
                    or not set(receipt['removed'])<=set(manifest)
                    or receipt['pending'] is not None and (receipt['pending'] not in manifest
                                                          or receipt['pending'] in receipt['removed'])):
                raise ArtifactError('Disposal journal does not match this archive')
        actual=output_files(output)
        removed=set(receipt['removed'])
        pending={receipt['pending']} if receipt['pending'] is not None else set()
        if actual-set(manifest) or actual&removed or set(manifest)-actual-removed-pending:
            raise ArtifactError('Output is changed or missing without a matching disposal journal')
        states={}
        # Finish every hash before deleting the first file. Revalidate the exact
        # inode/size/timestamps immediately before each unlink as well.
        for name in sorted(actual):
            states[name]=verified_file(output/name,manifest[name])
        if output_files(output)!=actual:raise ArtifactError('Output inventory changed during verification')
        if fingerprint(archive_file)!=archive_fp:raise ArtifactError('Archive changed during output verification')
        for name,item in evidence.items():
            if fingerprint(completed/name)!=item['fingerprint']:
                raise ArtifactError('Completion evidence changed during output verification')
        if not journal_path.exists():write_json(journal_path,receipt)
        if pending and not pending&actual:
            receipt['removed'].append(receipt['pending']);receipt['pending']=None
            write_json(journal_path,receipt,replace=True)
        for name in sorted(actual):
            receipt['pending']=name;write_json(journal_path,receipt,replace=True)
            with parent_fd(output/name) as (parent,base):
                current=os.stat(base,dir_fd=parent,follow_symlinks=False)
                if state_tuple(current)!=states[name]:raise ArtifactError('Output changed before deletion')
                os.unlink(base,dir_fd=parent);os.fsync(parent)
            receipt['removed'].append(name);receipt['pending']=None
            write_json(journal_path,receipt,replace=True)
        if output_files(output):raise ArtifactError('Output was added during disposal')
        for name,item in evidence.items():
            if fingerprint(completed/name)!=item['fingerprint']:
                raise ArtifactError('Original reports changed during disposal')
        receipt.update(passed=True,finished_at=instant(),removed_logical_bytes=sum(v['bytes'] for v in manifest.values()),
            original_evidence_unchanged=True, directories_preserved=True)
        write_json(journal_path,receipt,replace=True)
        return receipt
    finally:os.close(fd)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    compare=sub.add_parser('compare',help='Read small completed/archived reports only; never remove output')
    compare.add_argument('--case',action='append',default=[],metavar='A=PATH')
    compare.add_argument('--family',choices=tuple(CASE_FAMILIES),
                         help='Memory=A/B/C; CPU=A/D/E; default inferred from selected cases')
    compare.add_argument('--output',type=Path,required=True)
    save=sub.add_parser('archive',help='Preserve small evidence from a successful independent storage audit')
    save.add_argument('--completed',type=Path,required=True)
    save.add_argument('--audit',type=Path,required=True)
    save.add_argument('--output',type=Path,required=True)
    remove=sub.add_parser('dispose',help='Explicitly reverify and delete only archived completed bulk files')
    remove.add_argument('--archive',type=Path,required=True)
    remove.add_argument('--completed',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='archive':
            archive(args.completed,args.audit,args.output)
            print('Archived audited compact evidence: '+str(args.output/'archive.json'))
        elif args.command=='dispose':
            result=dispose(args.archive,args.completed)
            print(f"Disposed {len(result['removed'])} verified bulk files; reports, inputs and directories preserved.")
        else:
            cases={}
            for value in args.case:
                label,separator,path=value.partition('=')
                if not separator or label not in CASE_LIMITS or label in cases or not path:
                    raise ArtifactError('Specify each case once as LABEL=PATH, with a label A..E')
                cases[label]=Path(path)
            if not cases:raise ArtifactError('Select at least one case')
            output=ordinary(args.output)
            if output.exists() or ROOT in output.parents:raise ArtifactError('Choose a new report directory outside source trees')
            if any(output==p.absolute() or p.absolute() in output.parents for p in cases.values()):
                raise ArtifactError('Keep the comparison outside original evidence')
            result=comparison(cases,family=args.family)
            output.mkdir(mode=0o700,parents=True);write_attribution(output)
            write_json(output/'report.json',result)
            with (output/'comparison.md').open('x') as stream:
                os.chmod(output/'comparison.md',0o600)
                stream.write('<!-- (C) Copyright 2026, by Ross Richardson\n\nGenerated research benchmark comparison.\n@author ross richardson\n-->\n\n'+render(result))
            print('Wrote benchmark comparison: '+str(output/'comparison.md'))
            if any(p['unexpected_mismatches'] for p in result['comparisons']):return 1
        return 0
    except (ArtifactError,OSError,ValueError,KeyError,TypeError) as error:
        print('STOPPED: '+str(error),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
