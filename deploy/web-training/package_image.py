#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Create a fresh Docker context for configurable UK training SingleRun.

@author ross richardson
"""
import argparse
from pathlib import Path
import shutil
import zipfile


def package(repo, output):
    repo, output = repo.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(repo) or repo.is_relative_to(output):
        raise ValueError('Use a new context directory outside the repository')
    jar = repo / 'singlerun.jar'
    with zipfile.ZipFile(jar) as archive:
        if not {'simpaths/experiment/SimPathsTrainingStartup.class',
                'microsim/web/server/DatabaseQueryAccess.class',
                'microsim/web/server/BackendAuth.class',
                'microsim/web/server/WorkbookBudget.class'}.issubset(archive.namelist()):
            raise ValueError('Build the updated SimPaths jar first')
    selected = list((repo / 'input').glob('*.xlsx')) + list((repo / 'input').glob('*.xls'))
    for directory, suffixes in [('InitialPopulations/training', {'.csv'}),
                                ('EUROMODoutput/training', {'.txt', '.xlsx'})]:
        selected.extend(p for p in (repo / 'input' / directory).iterdir() if p.suffix.lower() in suffixes)
    for required in ['InitialPopulations/training/population_initial_UK_2019.csv',
                     'EUROMODoutput/training/EUROMODpolicySchedule.xlsx']:
        if repo / 'input' / required not in selected:
            raise ValueError(f'Missing supplied training file: {required}')
    deployment = repo / 'deploy/web-training'
    copies = [(p, p.relative_to(repo)) for p in selected]
    copies += [(repo / name, Path(name)) for name in ['singlerun.jar', 'webserver.properties', 'license.txt', 'COPYRIGHT.md']]
    copies += [(deployment / name, Path(name)) for name in ['Dockerfile', 'README.md']]
    for source, _ in copies:
        if source.is_symlink() or not source.is_file():
            raise ValueError(f'Expected regular file: {source}')
    size = sum(source.stat().st_size for source, _ in copies)
    ancestor = output.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < size:
        raise ValueError(f'Context requires at least {size / 1024**3:.2f} GiB free, plus Docker preparation space')
    output.mkdir(parents=True)
    for source, relative in copies:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    print(f'Context ready: {output}\nImage preparation needs additional Docker storage and at least 3 GiB Java heap.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package(Path(__file__).resolve().parents[2], args.output.expanduser())
