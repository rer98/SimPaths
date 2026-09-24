#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Build local training/user-data images with bounded temporary-file lifetime and
explicit opt-in Docker cache cleanup. Does not compile Java or run simulations.

@author ross richardson
"""
import argparse
import math
from pathlib import Path
import runpy
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
load_web_tool = runpy.run_path(str(ROOT / 'deploy/_tool_loader.py'))['load_web_tool']
PROFILES = ('training', 'user-data')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument('--profile', choices=('both',) + PROFILES, default='both')
    parser.add_argument('--work-root', type=Path, default=Path('/tmp/codex-rer'))
    parser.add_argument('--min-free-gib', type=float, default=8,
                        help='Minimum free space before each image (default: 8 GiB; not a quota)')
    parser.add_argument('--docker-storage-path', type=Path,
                        help='Existing local path on Docker storage filesystem; '
                             'otherwise use DockerRootDir reported by the daemon')
    parser.add_argument('--prune-build-cache', action='store_true',
                        help='Prune unused shared Docker build cache before and after builds; '
                             'never prune images, containers or volumes')
    parser.add_argument('--frontend', type=Path, help='JAS-mine-web checkout supplying shared tools; otherwise JASMINE_WEB_REPO or ~/git/JAS-mine/JAS-mine-web')
    args = parser.parse_args(argv)
    if not math.isfinite(args.min_free_gib) or args.min_free_gib <= 0:
        parser.error('--min-free-gib must be finite and positive')
    args.repo = args.repo.expanduser().resolve()
    args.work_root = args.work_root.expanduser().resolve()
    profiles = PROFILES if args.profile == 'both' else (args.profile,)
    for profile in profiles:
        if not (args.repo / f'deploy/web-{profile}/package_image.py').is_file():
            parser.error(f'Missing packager for {profile} in {args.repo}')
    if not (args.repo / 'singlerun.jar').is_file():
        parser.error('Build SimPaths with Maven first: singlerun.jar is missing')

    shared = load_web_tool('local_images.py', args.frontend)
    jobs = [shared.ImageBuild(
        name=profile, image=f'simpaths-interactive:uk-{profile}',
        package_command=(sys.executable, str(args.repo / f'deploy/web-{profile}/package_image.py')),
    ) for profile in profiles]
    return shared.build_images(
        jobs, work_root=args.work_root, min_free_gib=args.min_free_gib,
        docker_storage_path=args.docker_storage_path, prune_build_cache=args.prune_build_cache)


def interrupted(signum, frame):
    raise KeyboardInterrupt


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Build interrupted; temporary context cleanup attempted.', file=sys.stderr)
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'Build stopped: {error}', file=sys.stderr)
        sys.exit(1)
