#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Create a new, explicit Docker context from a verified Quick Start package.

@author ross richardson
"""
import argparse
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
load_tool = runpy.run_path(str(HERE.parent / '_tool_loader.py'))['load_tool']

preparation = load_tool('web-quickstart/prepare_profile.py', REPO)
digest, write_json = preparation.digest, preparation.write_json
PROFILE, validate_destination = preparation.PROFILE, preparation.validate_destination
profile_for, profile_id, profile_readme = preparation.profile_for, preparation.profile_id, preparation.profile_readme


def release_dockerfile(template, base_tag):
    """Refresh OS packages even when retaining a prepared population base."""
    header = template[:template.index('FROM ')]
    package_setup = template[template.index('\n', template.index('FROM ')) + 1:
                             template.index('WORKDIR /app')]
    # Reuse the fresh-image package installation and minimum-version checks.
    return (header + f'FROM {base_tag}\nUSER root\n' + package_setup
            + 'WORKDIR /app\nCOPY singlerun.jar /app/app.jar\n'
            + 'COPY webserver.properties README.md license.txt COPYRIGHT.md release.json /app/\n'
            + 'RUN rm -f /app/query-access.json\n'
            + 'RUN java -cp /app/app.jar microsim.web.server.DatabaseQueryAccess /app/input/input /app/query-access.json'
            + ' && chown -R 10001:10001 /app/input\n'
            # Older prepared bases predate non-root execution. Reuse the full
            # runtime setup, including writable output and protected code files.
            + template[template.index('RUN mkdir -p output'):])


def package(source, repo, output):
    source, repo = source.resolve(), repo.resolve()
    validate_destination(output, repo, source)
    checks = json.loads((source / 'checksums.json').read_text())
    for relative, expected in checks.items():
        path = source / relative
        if path.is_symlink() or not path.resolve().is_relative_to(source) or digest(path) != expected:
            raise ValueError(f'Package integrity check failed: {relative}')
    receipt = json.loads((source / 'profile.json').read_text())
    population = receipt.get('profile', {}).get('requested_population')
    if (receipt.get('format_version') != 1 or receipt.get('profile') != profile_for(population)
            or receipt.get('profile_id') != profile_id(population)):
        raise ValueError('Unsupported prepared profile')
    jar = repo / 'singlerun.jar'
    with zipfile.ZipFile(jar) as archive:
        if not {'simpaths/experiment/SimPathsQuickStart.class',
                'microsim/web/server/DatabaseQueryAccess.class',
                'microsim/web/server/BackendAuth.class',
                'microsim/web/server/WorkbookBudget.class'}.issubset(archive.namelist()):
            raise ValueError('Rebuild SimPaths with the prepared Quick Start integration first')
    # Runtime consumes Excel parameters and the prepared database. Raw donor text
    # and population CSV sources remain in the original preparation package.
    selected = [source / relative for relative in checks
                if relative.startswith('input/') and
                (relative == 'input/input.mv.db' or Path(relative).suffix.lower() in ('.xls', '.xlsx'))]
    if source / 'input/input.mv.db' not in selected:
        raise ValueError('Package checksum manifest does not include the prepared database')
    for required in ('profile.json', 'README.md', 'input/EUROMODoutput/training/EUROMODpolicySchedule.xlsx'):
        if required not in checks:
            raise ValueError(f'Package checksum manifest is missing {required}')
    size = sum(p.stat().st_size for p in selected) + jar.stat().st_size
    ancestor = output.absolute().parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < size + 1024**3:
        raise ValueError('Insufficient space for context and 1 GiB reserve')
    output.mkdir(parents=True, exist_ok=False)
    for path in selected + [source / 'profile.json', source / 'README.md']:
        target = output / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if digest(target) != checks[str(path.relative_to(source))]:
            raise ValueError(f'Package changed while copying: {path}')
    for name in ('singlerun.jar', 'webserver.properties', 'license.txt', 'COPYRIGHT.md'):
        shutil.copy2(repo / name, output / name)
    # The runtime instructions describe this launcher revision; the preparation
    # package retains its original README and transfer checksum unchanged.
    (output / 'README.md').write_text(profile_readme(population))
    shutil.copy2(HERE / 'Dockerfile', output / 'Dockerfile')
    (output / '.dockerignore').write_text('# (C) Copyright 2026, by Ross Richardson\n# Quick Start Docker build context allowlist.\n# @author ross richardson\n*\n!Dockerfile\n!.dockerignore\n!singlerun.jar\n'
        '!webserver.properties\n!README.md\n!license.txt\n!COPYRIGHT.md\n!release.json\n!profile.json\n!input/\n!input/**\n')
    try:
        revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        revision = None
    write_json(output / 'release.json', dict(profile_id=receipt['profile_id'],
        runtime_jar_sha256=digest(output / 'singlerun.jar'), simpaths_revision=revision,
        attribution_sha256=digest(output / 'COPYRIGHT.md')))
    write_json(output / 'context.json', dict(profile_id=receipt['profile_id'],
               preparation_jar_sha256=receipt['jar_sha256'], runtime_jar_sha256=digest(output / 'singlerun.jar'),
               input_files=[str(p.relative_to(source)) for p in selected]))
    print(f'Context ready: {output}\nBuild explicitly: docker build -t simpaths-quickstart:uk-2019-{population} {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package(args.package.expanduser(), args.repo.expanduser(), args.output.expanduser())


if __name__ == '__main__':
    main()
