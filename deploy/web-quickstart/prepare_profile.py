#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Prepare and verify the fixed SimPaths training Quick Start outside source trees.

@author ross richardson
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PROFILE = dict(country='UK', start_year=2019, end_year=2026,
               requested_population=50000, seed=606, include_observer=True,
               use_weights=False, ignore_population_targets=False,
               training_data=True)


def profile_for(population):
    if population not in (20000, 50000):
        raise ValueError('Quick Start supports 20000 or 50000 people')
    return dict(PROFILE, requested_population=population)


def profile_id(population):
    profile_for(population)
    return f'uk-2019-training-{population}-seed606'


def profile_readme(population):
    profile_for(population)
    return (f'# SimPaths UK Quick Start — {population:,} people\n\n'
            f'This session uses a prepared UK population of **{population:,} requested people**, starting in 2019.\n\n'
            + (HERE / 'README.md').read_text())


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def selected_inputs(source):
    """Explicitly select Excel parameters and supplied UK training sources, never DBs."""
    paths = sorted(p for p in source.iterdir()
                   if p.is_file() and p.suffix.lower() in ('.xls', '.xlsx'))
    population = source / 'InitialPopulations/training/population_initial_UK_2019.csv'
    donor = source / 'EUROMODoutput/training'
    paths += [population, donor / 'EUROMODpolicySchedule.xlsx',
              donor / 'DatabaseCountryYear.xlsx']
    paths += [donor / f'uk_{year}_std.txt' for year in range(2011, 2027)]
    for path in paths:
        if not path.is_file():
            raise ValueError(f'Missing required input: {path}')
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError(f'Input must be a regular file within the input tree: {path}')
    if not any(p.parent == source for p in paths):
        raise ValueError('No top-level Excel parameters found')
    return paths


def validate_destination(output, repo, inputs):
    if output.exists() or output.is_symlink():
        raise ValueError(f'Output already exists; choose a new directory: {output}')
    for protected in (REPO, repo, inputs):
        if output.resolve().is_relative_to(protected.resolve()):
            raise ValueError(f'Output must be outside source trees: {protected}')


def execute(command, cwd, log, timeout):
    with log.open('w') as stream:
        process = subprocess.Popen(command, cwd=cwd, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
    if code:
        raise RuntimeError(f'Process exited {code}; see {log}')


def properties(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line and not line.startswith('#'))


def check_loading(log, verified, prepared, loaded):
    if 'Found processed dataset - preparing for simulation' not in log.read_text():
        raise RuntimeError('Loading check did not report processed-population reuse')
    for key in ('person', 'household', 'benefitunit'):
        if not (int(verified[key]) > 0 and verified[key] == prepared[key] == loaded[key]):
            raise RuntimeError(f'Prepared/verified/loaded count mismatch: {key}')


def prepare(args):
    population = getattr(args, "population", 50000)
    profile = profile_for(population)
    repo = args.repo.expanduser().resolve()
    inputs = repo / 'input'
    jar = repo / 'singlerun.jar'
    output = args.output.expanduser().absolute()
    validate_destination(output, repo, inputs)
    files = selected_inputs(inputs)
    if not jar.is_file():
        raise ValueError(f'Build SimPaths first; missing {jar}')
    if args.timeout <= 0:
        raise ValueError('--timeout must be positive')
    for tool in ('java', 'javac'):
        if shutil.which(tool) is None:
            raise ValueError(f'{tool} is required (use Java 25)')
    if args.dry_run:
        print(json.dumps(dict(output=str(output), profile=profile,
                             inputs=[str(p.relative_to(inputs)) for p in files],
                             jar=str(jar), stages=['prepare', 'verify', 'load', 'package']), indent=2))
        return
    ancestor = output.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < 7 * 1024**3:
        raise ValueError('At least 7 GiB free space is required for preparation and validation copies')
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    status = output / 'status.json'
    write_json(status, dict(status='running', profile=profile))
    try:
        logs = output / 'logs'
        logs.mkdir()
        tools = output / 'tools'
        tools.mkdir()
        classes = tools / 'classes'
        classes.mkdir()
        frozen_jar = tools / 'simpaths.jar'
        jar_hash = digest(jar)
        shutil.copy2(jar, frozen_jar)
        if digest(frozen_jar) != jar_hash:
            raise RuntimeError('JAR changed while copying')
        helper = HERE / 'PrepareQuickStart.java'
        shutil.copy2(helper, tools / helper.name)
        shutil.copy2(Path(__file__), tools / Path(__file__).name)
        work = output / 'work'
        work.mkdir()
        provenance = {}
        for source in files:
            relative = source.relative_to(inputs)
            dest = work / 'input' / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            before = digest(source)
            shutil.copy2(source, dest)
            if digest(dest) != before:
                raise RuntimeError(f'Input changed while copying: {source}')
            provenance[str(relative)] = before
        write_json(output / 'source-inputs.json', provenance)
        execute(['javac', '-cp', str(frozen_jar), '-d', str(classes),
                 str(tools / helper.name)], tools, logs / 'compile.log', args.timeout)
        java = ['java', '-Djava.awt.headless=true', '-Xmx4g', '-cp',
                os.pathsep.join((str(classes), str(frozen_jar))),
                'simpaths.experiment.PrepareQuickStart']
        for mode in ('prepare', 'verify'):
            print(f'{mode}: see {logs / (mode + ".log")}', flush=True)
            execute(java + [mode, str(population)], work, logs / f'{mode}.log', args.timeout)
        verified = properties(work / 'verified.properties')
        prepared = properties(work / 'build.properties')
        # This workspace is disposable; never point loading at the package itself.
        loading = output / 'loading-check'
        loading.mkdir()
        shutil.copytree(work / 'input', loading / 'input')
        print(f'load: see {logs / "load.log"}', flush=True)
        execute(java + ['load', str(population)], loading, logs / 'load.log', args.timeout)
        loaded = properties(loading / 'build.properties')
        check_loading(logs / 'load.log', verified, prepared, loaded)
        # Build may rewrite Excel files, so publish the preparation workspace,
        # not the separate loading workspace. Move avoids a further large DB copy.
        candidate = output / 'package.pending'
        candidate.mkdir()
        (work / 'input').rename(candidate / 'input')
        (candidate / 'README.md').write_text(profile_readme(population))
        revision = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True, check=False)
        dirty = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain'],
                               capture_output=True, text=True, check=False)
        # Provision before checksums: this changes only H2 access metadata in the
        # new package; the independent loading check above keeps its original evidence.
        execute(['java', '-cp', str(frozen_jar), 'microsim.web.server.DatabaseQueryAccess',
                 str(candidate / 'input/input')], candidate, logs / 'query-access.log', args.timeout)
        receipt = dict(format_version=1, profile_id=profile_id(population),
                       profile=profile, prepared_at=datetime.now(timezone.utc).isoformat(),
                       actual_counts={k: int(verified[k]) for k in verified},
                       source_revision=revision.stdout.strip() if revision.returncode == 0 else None,
                       source_worktree_status=dirty.stdout if dirty.returncode == 0 else None,
                       jar_sha256=jar_hash, helper_sha256=digest(tools / helper.name),
                       preparation_build_seconds=float(prepared['buildSeconds']),
                       loading_build_seconds=float(loaded['buildSeconds']),
                       fresh_jvm_loading_verified=True,
                       simulated_years_run=0)
        write_json(candidate / 'profile.json', receipt)
        write_json(candidate / 'checksums.json', {
            str(p.relative_to(candidate)): digest(p)
            for p in sorted(candidate.rglob('*')) if p.is_file()})
        candidate.rename(output / 'package')
        write_json(status, dict(status='complete', package=str(output / 'package')))
        print(f'Complete: {output / "package"}', flush=True)
    except BaseException as failure:
        write_json(status, dict(status='failed', error=str(failure)))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--population', type=int, choices=(20000, 50000), default=50000)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=3600,
                        help='Maximum seconds per subprocess (default: 3600)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Validate paths and show selected inputs; create no files')
    args = parser.parse_args()
    try:
        prepare(args)
    except (Exception, KeyboardInterrupt) as failure:
        print(f'Preparation failed: {failure}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
