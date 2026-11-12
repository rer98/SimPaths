"""(C) Copyright 2026, by Ross Richardson

Verify fixed source-summary promises before capture and inactive activation.
No deadline renewal, cache writes, source parsing or scientific execution.
@author ross richardson
"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .artifacts import ArtifactError


def source_rows(connection, queue):
    """Use the backup's SQL snapshot and current aggregate-access rules."""
    from psycopg import sql
    schema = queue.schema
    return connection.execute(sql.SQL('''SELECT s.owner_id,s.identity,s.source,
        s.summary_protected_at,s.summary_expires_at,s.summary_backend
        FROM {} s JOIN {} a ON a.id=s.attempt_id JOIN {} j ON j.id=a.job_id
        LEFT JOIN {} d ON d.attempt_id=a.id
        WHERE s.pool_id=%s AND s.summary_expires_at>clock_timestamp()
          AND j.state='succeeded' AND a.outcome='success'
          AND (d.reason IS NULL OR d.reason IN ('expiry','raw_only'))
          AND {}(s.pool_id,s.owner_id,clock_timestamp())
          AND {}(s.pool_id,s.owner_id,j.dataset_id,clock_timestamp())
          AND NOT EXISTS (SELECT 1 FROM {} later WHERE later.job_id=j.id AND later.number>a.number)
        ORDER BY s.attempt_id LIMIT 4097''').format(
            *[sql.Identifier(schema, table) for table in
              ('visualiser_sources', 'attempts', 'jobs', 'output_deletions')],
            sql.Identifier(schema, 'account_active'),
            sql.Identifier(schema, 'dataset_authorised'),
            sql.Identifier(schema, 'attempts')), (queue.pool_id,)).fetchall()


def verify_bundles(state, rows, *, now=None):
    """Caller owns either the summary-cache guard or inactive destination guard.

    Hash the exact immutable seed identities, and require admitted holds that
    cover the entire SQL promise rather than merely being unexpired today.
    """
    from .visualiser_backend import VisualiserBackend
    from .visualiser_summary_cache import SummaryCache
    from .visualiser_summary_holds import scope
    if now is None:
        now = datetime.now(timezone.utc).timestamp()
    if len(rows) > 4096:
        raise ArtifactError('Retained source inventory exceeds its bound')
    current = [row for row in rows if row['summary_expires_at'].timestamp() > now]
    if not current:
        return dict(sources=0, seeds=0)
    root = Path(state) / 'execution/visualiser-cache/summaries'
    if not root.is_dir() or root.is_symlink():
        raise ArtifactError('Promised retained summaries are unavailable')
    cache = SummaryCache(root, maximum_entries=4096)
    verified = 0
    try:
        for row in current:
            description = row['summary_backend']
            files = description['calculation']['files']
            if set(files) != {'runner.cjs', 'calculation.cjs'}:
                raise ValueError('Incomplete retained calculation identity')
            adapter = SimpleNamespace(manifest=dict(files=files),
                                      identity=dict(node=description['node']))
            source = deepcopy(row['source'])
            source.update(owner=row['owner_id'], identity=row['identity'])
            configuration = source['configuration']
            repetitions = row['identity']['repetitions']
            if ([str(run['seed']) for run in configuration['runs']] !=
                    [str(run['seed']) for run in repetitions]):
                raise ValueError('Retained repetition identity changed')
            identities = [VisualiserBackend._summary_identity(adapter, source, run,
                VisualiserBackend._summary_inputs(source, run))
                for run in configuration['runs']]
            deadline = row['summary_expires_at'].timestamp()
            records = {key: record for key, record in cache.holds.load().items()
                       if record['expires_at'] >= deadline}
            admitted = cache.holds.units(records)
            if any(not cache.holds.matches(identity, admitted) for identity in identities):
                raise ValueError('Summary protection ends before the fixed source deadline')
            if any(scope(identity) != scope(identities[0]) for identity in identities):
                raise ValueError('Retained calculation identities differ')
            cache._verify_retained_locked(identities)
            verified += len(identities)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise ArtifactError('Fixed source-summary protection could not be verified') from error
    return dict(sources=len(current), seeds=verified)


def verify(connection, queue, state):
    return verify_bundles(state, source_rows(connection, queue))
