"""(C) Copyright 2026, by Ross Richardson

Resolve maintainer checkouts and catalogue images for SimPaths web workflows.

@author ross richardson
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def frontend_path():
    return Path(os.environ.get('JASMINE_WEB_REPO') or
                Path.home() / 'git/JAS-mine/JAS-mine-web').expanduser().resolve()


def catalogue_path(frontend, catalogue=None):
    return (Path(catalogue) if catalogue is not None else
            Path(frontend) / 'deploy/simpaths/models.json').expanduser().resolve()


def catalogue_model(path, model_id):
    models = json.loads(Path(path).read_text())['models']
    matches = [model for model in models if model.get('id') == model_id]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one {model_id} entry in {path}')
    if not matches[0].get('deployment', {}).get('image'):
        raise ValueError(f'Missing image for {model_id} in {path}')
    return matches[0]


def quickstart_image(frontend, population, image=None, catalogue=None):
    if image:
        return image
    path = catalogue_path(frontend, catalogue)
    return catalogue_model(path, f'simpaths-quickstart-{population}')['deployment']['image']
