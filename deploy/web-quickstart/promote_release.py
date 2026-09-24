#!/usr/bin/env python3
"""(C) Copyright 2026, by Ross Richardson

Promote accepted SimPaths Quick Start releases using shared catalogue retention tools.

@author ross richardson
"""
import argparse
from pathlib import Path
import re
import runpy

loader = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))
workflow = loader['load_tool']('_workflow.py')
RELEASE = re.compile(r'^simpaths-quickstart:uk-2019-(20000|50000)-release-(\d{8}-\d{6})$')


def model_for_tag(tag):
    match = RELEASE.fullmatch(tag)
    return f'simpaths-quickstart-{match.group(1)}' if match else None


def main(argv=None, *, reviewed_historical=None, keep_images=()):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, default=workflow.frontend_path())
    parser.add_argument('--catalogue', type=Path, help='Defaults to FRONTEND/deploy/simpaths/models.json')
    parser.add_argument('--evidence', type=Path, default=Path.home() / 'simpaths-benchmarks')
    parser.add_argument('--image', action='append', default=[], help='Validated release to promote; repeat for both populations')
    parser.add_argument('--protect-catalogue', action='append', type=Path, default=[])
    parser.add_argument('--keep-image', action='append', default=[], help='Additional tag or ID to retain, including prepared bases')
    parser.add_argument('--apply', action='store_true', help='Otherwise preview only')
    if reviewed_historical is not None:
        parser.add_argument('--include-historical', action='store_true', help='Retire explicitly reviewed historical coordinator artifacts')
    args = parser.parse_args(argv)
    shared = loader['load_web_tool']('release_images.py', args.frontend)
    import docker
    client = docker.from_env()
    try:
        return shared.promote_images(
            client, catalogue_path=workflow.catalogue_path(args.frontend, args.catalogue),
            frontend=args.frontend, evidence_root=args.evidence, model_for_tag=model_for_tag,
            images=args.image, protect_catalogues=args.protect_catalogue,
            keep_images=[*keep_images, *args.keep_image], apply=args.apply,
            reviewed_historical=reviewed_historical if getattr(args, 'include_historical', False) else None)
    finally:
        client.close()


if __name__ == '__main__':
    main()
