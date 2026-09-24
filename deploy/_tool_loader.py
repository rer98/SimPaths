"""(C) Copyright 2026, by Ross Richardson

Load model scripts and explicitly selected shared web tools without module-name clashes.

@author ross richardson
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import sys


def load_tool(relative, repo=None):
    deployment = (Path(repo).resolve() if repo is not None else Path(__file__).resolve().parents[1]) / 'deploy'
    return _load(relative, deployment, '_simpaths_deploy_')


def load_web_tool(relative, frontend=None):
    """Load shared offline tooling, without importing the frontend application."""
    checkout = Path(frontend or os.environ.get('JASMINE_WEB_REPO') or
                    Path.home() / 'git/JAS-mine/JAS-mine-web').expanduser().resolve()
    deployment = checkout / 'jasmine_web/deployment'
    if not (deployment / relative).is_file():
        raise FileNotFoundError(
            f'Missing shared deployment tool: {deployment / relative}. '
            'Update JAS-mine-web and select its checkout with --frontend PATH '
            'or JASMINE_WEB_REPO.')
    return _load(relative, deployment, '_jasmine_web_deploy_')


def _load(relative, deployment, prefix):
    path = (deployment / relative).resolve()
    if not path.is_relative_to(deployment) or not path.is_file():
        raise ValueError(f'Missing deployment tool or path outside its directory: {path}')
    name = prefix + hashlib.sha256(str(path).encode()).hexdigest()
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module
