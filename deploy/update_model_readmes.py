#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Replace only the README layer of current model images, then update the catalogue.
Existing runtime/data layers and image configuration must remain unchanged.

@author ross richardson
"""
import argparse
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
loader = runpy.run_path(str(ROOT / 'deploy/_tool_loader.py'))
load_tool, load_web_tool = loader['load_tool'], loader['load_web_tool']
PROFILES = ('simpaths-quickstart-20000', 'simpaths-quickstart-50000',
            'simpaths-uk-training', 'simpaths-uk-user-data')
RETAINED_ALIASES = ('simpaths-interactive:uk-training', 'simpaths-interactive:uk-user-data')


def guides(repo):
    profile_readme = load_tool('web-quickstart/prepare_profile.py', repo).profile_readme
    return {
        PROFILES[0]: profile_readme(20000).encode('utf-8'),
        PROFILES[1]: profile_readme(50000).encode('utf-8'),
        PROFILES[2]: (repo / 'deploy/web-training/README.md').read_bytes(),
        PROFILES[3]: (repo / 'deploy/web-user-data/README.md').read_bytes(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalogue', type=Path, default=Path.home() / 'git/JAS-mine/JAS-mine-web/deploy/simpaths/models.json')
    parser.add_argument('--repo', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, help='New evidence directory; defaults beneath ~/simpaths-benchmarks')
    parser.add_argument('--apply', action='store_true', help='Build, verify and update catalogue; otherwise preview')
    parser.add_argument('--frontend', type=Path, help='JAS-mine-web checkout supplying shared tools; otherwise JASMINE_WEB_REPO or ~/git/JAS-mine/JAS-mine-web')
    args = parser.parse_args(argv)
    readmes = guides(args.repo.expanduser().resolve())
    for key, content in readmes.items():
        if not content.startswith(b'# SimPaths') and not content.startswith(b'<!--'):
            raise ValueError(f'Unexpected guide for {key}')
    shared = load_web_tool('readme_images.py', args.frontend)
    return shared.update_readmes(
        args.catalogue, readmes, apply=args.apply, output=args.output,
        output_root=Path.home() / 'simpaths-benchmarks',
        image_repository='simpaths-readme', retained_aliases=RETAINED_ALIASES)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        if error.stderr:
            print(error.stderr.decode('utf-8', errors='replace'), file=sys.stderr)
        raise SystemExit(f'Docker command failed: {error}; see the build log if one was written')
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit(str(error))
