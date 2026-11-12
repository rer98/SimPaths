"""(C) Copyright 2026, by Ross Richardson

Omit temporary pipeline scratch and rebuild completion promises on inactive
restoration from verified SQL identities and protected per-seed summary receipts.
No source filesystem project IDs or root-owned quota ledgers are restored.
@author ross richardson
"""
from pathlib import Path
import re
import stat

from .artifacts import ArtifactError
from .backup_files import read_metadata

SCRATCH = re.compile(r'pipeline-batch-[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
SUMMARY = re.compile('[a-f0-9]{64}')


def scratch_exclusions(state):
    """Offline backup already excludes all live processors before this scan."""
    root = Path(state) / 'artifacts'
    if not root.exists() and not root.is_symlink():
        return ()
    if root.is_symlink():
        raise ArtifactError('Linked pipeline artifact root')
    result = []
    for child in root.iterdir():
        if not SCRATCH.fullmatch(child.name):
            continue
        info = child.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise ArtifactError('Invalid temporary pipeline scratch')
        result.append('artifacts/' + child.name)
    return tuple(sorted(result))


def summary_index(state):
    """Read only bounded committed metadata; backup verification checks bytes."""
    root = Path(state) / 'execution/visualiser-cache/summaries'
    if not root.exists():
        return None, {}
    from .visualiser_summary_cache import SummaryCache
    cache = SummaryCache(root, maximum_entries=4096)
    groups = {}
    for child in sorted(root.iterdir()):
        if not SUMMARY.fullmatch(child.name):
            continue
        identity = read_metadata(child / 'receipt.json')['identity']
        execution = identity.get('execution', {})
        # Older, incomplete provenance cannot discharge a new physical promise.
        fields = ('job', 'attempt', 'execution_key', 'prepared')
        if (not isinstance(execution, dict) or any(not execution.get(k) for k in fields)
                or not identity.get('calculation') or not identity.get('node')):
            continue
        from .visualiser_summary_holds import scope
        group = (identity.get('owner'), identity.get('configuration'),
                 *(execution[k] for k in fields), scope(identity))
        groups.setdefault(group, []).append(identity)
        if sum(map(len, groups.values())) > 4096:
            raise ArtifactError('Restored summary inventory exceeds its bound')
    return cache, groups


def protected(cache, groups, row, seeds):
    """Caller holds exclusive inactive-state and frozen-database guards.

    No target processor can exist, so use the read-only verifier under that
    broader guard without creating a cache lock absent from the backup manifest.
    A failed activation remains byte-for-byte retryable.
    """
    if cache is None:
        return False
    expected = (str(row['owner_id']), row['configuration_id'], str(row['job_id']),
                str(row['id']), row['execution_key'], row['prepared_fingerprint'])
    from .visualiser_summary_holds import SummaryCapacityError
    for group, identities in groups.items():
        if group[:-1] != expected:
            continue
        if len(identities) != len(seeds) or {str(i.get('seed')) for i in identities} != set(seeds):
            continue
        try:
            cache._verify_retained_locked(identities)
        except SummaryCapacityError:
            continue  # Expired/unadmitted holds cannot replace raw preparation.
        return True
    return False


def restore_promises(c, queue, target, quotas):
    """Fund unfinished retained successful sources before removing the gate.

    The destination broker proves that each workspace is inactive and belongs to
    its fresh ledger. A shortfall preserves both the inactive activation gate and
    any promises already funded; a retry reuses their exact immutable identities.
    """
    from psycopg import sql
    from jasmine_web.batch.pipeline_storage import promise
    quotas.pipeline_ready()
    cache, groups = summary_index(target)
    rows = c.execute(sql.SQL('''SELECT a.id,a.job_id,a.execution_key,e.owner_id,
        j.configuration_id,j.prepared_fingerprint FROM {} a JOIN {} j ON j.id=a.job_id
        JOIN {} e ON e.id=j.experiment_id
        WHERE a.pool_id=%s AND a.phase='finished' AND a.outcome='success'
        ORDER BY a.execution_key''').format(sql.Identifier(queue.schema, 'attempts'),
            sql.Identifier(queue.schema, 'jobs'),sql.Identifier(queue.schema, 'experiments')),
        (queue.pool_id,)).fetchall()
    funded = 0
    for row in rows:
        output = Path(target) / 'execution' / row['execution_key'] / 'work/output'
        if output.is_symlink():
            raise ArtifactError('Linked restored raw output')
        if not output.exists():
            continue  # Raw-only removal and normal expiry retain no future read.
        repetitions = c.execute(sql.SQL('''SELECT actual_seed FROM {} WHERE attempt_id=%s
            AND actual_seed IS NOT NULL ORDER BY ordinal''').format(
                sql.Identifier(queue.schema, 'repetitions')), (row['id'],)).fetchall()
        seeds = [str(r['actual_seed']) for r in repetitions]
        if not seeds or len(set(seeds)) != len(seeds):
            raise ArtifactError('Restored successful output has no complete distinct seed receipts')
        if protected(cache, groups, row, seeds):
            continue
        wanted = promise(row['execution_key'], len(seeds))
        quotas.restore_pipeline(wanted.key, wanted.token, wanted.size)
        funded += 1
    return dict(funded=funded)
