"""(C) Copyright 2026, by Ross Richardson
Read approved Visualiser editions and verify their bindings in hosted builds.
@author ross richardson
"""
import json
from pathlib import Path

REGISTRY = Path(__file__).with_name('visualiser') / 'historical' / 'approved.json'
CALCULATOR_FILES = ('runner.cjs', 'calculation.cjs')


def historical_editions(manifest):
    """Return (edition, filename prefix) for modern lists and original single-reader builds."""
    legacy = 'historical' in manifest
    if legacy and 'historical_editions' in manifest:
        raise ValueError('Ambiguous historical Visualiser inventory')
    history = [manifest['historical']] if legacy else manifest.get('historical_editions', [])
    if not isinstance(history, list):
        raise ValueError('Invalid historical Visualiser inventory')
    if not history:
        return []
    approved = {row['revision']: {k: row[k] for k in ('revision', 'node', 'files')}
                for row in json.loads(REGISTRY.read_text())}
    result = []
    seen = set()
    for edition in history:
        if not isinstance(edition, dict) or not isinstance(edition.get('revision'), str):
            raise ValueError('Invalid historical Visualiser edition')
        revision = edition['revision']
        if revision in seen or revision not in approved or edition != approved[revision]:
            raise ValueError('Unrecognised historical Visualiser calculator')
        prefix = 'historical-' if legacy else 'historical-' + revision + '-'
        if any(manifest['files'].get(prefix + name) != edition['files'][name] for name in CALCULATOR_FILES):
            raise ValueError('Historical calculator files differ from the approved edition')
        result.append((edition, prefix))
        seen.add(revision)
    return result
