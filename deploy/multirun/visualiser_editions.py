"""(C) Copyright 2026, by Ross Richardson
Read approved Visualiser editions and verify their bindings in hosted builds.
@author ross richardson
"""
import base64
import hashlib
import json
import re
from pathlib import Path

REGISTRY = Path(__file__).with_name('visualiser') / 'historical' / 'approved.json'
CALCULATOR_FILES = ('runner.cjs', 'calculation.cjs')
PROTECTED_SOURCE = """s.summary_expires_at > statement_timestamp()
    AND NOT EXISTS (SELECT 1 FROM output_deletions d WHERE d.pool_id=s.pool_id
        AND d.attempt_id=s.attempt_id AND d.reason NOT IN ('raw_only','expiry'))"""
LEGACY_SOURCES = """FROM output_deletions d
    JOIN visualiser_sources s ON s.pool_id=d.pool_id AND s.attempt_id=d.attempt_id
    WHERE d.pool_id=%s AND d.reason IN ('expiry','raw_only')
        AND d.summary_expires_at > statement_timestamp() AND s.summary_backend IS NULL"""


def calculator_usage(connection, pool, backend):
    """Count protected sources for the installed build using the retirement check's identity."""
    return manifest_usage(connection, pool, backend.manifest, backend.identity['node'])


def manifest_usage(connection, pool, manifest, node):
    rows = connection.execute(f"""SELECT s.summary_backend->'calculation'->'files' AS files,
            s.summary_backend->'calculation'->>'mode' AS mode,
            s.summary_backend->>'node' AS node, count(*) AS sources
        FROM visualiser_sources s WHERE s.pool_id=%s AND {PROTECTED_SOURCE}
        GROUP BY 1,2,3
        UNION ALL
        SELECT NULL, NULL, NULL, count(*) {LEGACY_SOURCES}""", (pool, pool)).fetchall()
    files = {name: manifest['files'][name] for name in CALCULATOR_FILES}
    editions = [dict(revision=manifest['revision'], current=True, sources=0)]
    history = historical_editions(manifest)
    editions.extend(dict(revision=edition['revision'], current=False, sources=0)
                    for edition, _ in history)
    unmatched = 0
    for row in rows:
        matches_current = (row['files'] == files and row['mode'] == manifest['mode']
                           and row['node'] == node)
        if matches_current:
            editions[0]['sources'] += row['sources']
        matches_history = False
        # Retirement protects all records with these calculator files, including
        # records whose remaining metadata would prevent reuse with this runtime.
        for entry, (edition, _) in zip(editions[1:], history):
            if row['files'] == edition['files']:
                entry['sources'] += row['sources']
                matches_history = True
        if not matches_current and not matches_history:
            unmatched += row['sources']
    return dict(editions=editions, unmatched=unmatched)


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


REFERENCE_FILES = ('build.json', *CALCULATOR_FILES)


def reference_bytes(build, name):
    if name not in REFERENCE_FILES:
        raise ValueError('Unknown Visualiser reference file')
    limit = (1 if name == 'build.json' else 8) * 1024**2
    with (build/name).open('rb') as source:
        data = source.read(limit+1)
    if len(data) > limit:
        raise ValueError('Reference file exceeds the Visualiser build limit')
    return data


def read_reference(build):
    files = {name: reference_bytes(build, name) for name in REFERENCE_FILES}
    manifest = json.loads(files['build.json'])
    if (manifest['format'] != 'simpaths.visualiser.build.v1'
            or not re.fullmatch('[0-9a-f]{40}', manifest['revision'])
            or any(hashlib.sha256(files[name]).hexdigest() != manifest['files'][name] for name in CALCULATOR_FILES)):
        raise ValueError('The reference calculator differs from its build manifest')
    return manifest, hashlib.sha256(files['build.json']).hexdigest(), files


def installed_reference(queue, build, name, expected):
    from jasmine_web.batch.policy import Conflict
    body = reference_bytes(build, 'build.json')
    digest = hashlib.sha256(body).hexdigest()
    manifest = json.loads(body)
    if expected and expected != digest:
        raise Conflict('The installed reference changed during transfer')
    with queue._connection() as c:
        row = c.execute("""SELECT EXISTS (SELECT 1 FROM visualiser_installations i
            JOIN visualiser_update_reviews r USING (pool_id,candidate)
            WHERE i.pool_id=%s AND i.state='installed' AND r.evidence->>'manifest'=%s
                AND r.evidence->>'visualiser'=%s)
            AND NOT EXISTS (SELECT 1 FROM visualiser_installations
                WHERE pool_id=%s AND state IN ('installing','recovery_required')) AS verified""",
            (queue.pool_id, digest, manifest['revision'], queue.pool_id)).fetchone()
    if not row['verified']:
        raise Conflict('The served Visualiser has no settled, verified installation receipt')
    if name != 'build.json':
        body = reference_bytes(build, name)
        if hashlib.sha256(body).hexdigest() != manifest['files'][name]:
            raise Conflict('The installed calculator changed during transfer')
    return dict(manifest=digest, data=base64.b64encode(body).decode('ascii'))
