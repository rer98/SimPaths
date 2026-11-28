"""(C) Copyright 2026, by Ross Richardson
Expire superseded Visualiser rollback bundles while keeping installation receipts.
@author ross richardson
"""
import json
import shutil
from uuid import UUID

from .. import update_package as package
from .bundle_installation import FORMAT, checked_bundle, manifest_digest
from .stage_bundle import sync_directory


def read_plan(bundle, digest):
    directory = package.ROOT / 'visualiser-updates' / str(UUID(str(bundle)))
    plan_bytes = package.trusted(directory / 'plan.json').read_bytes()
    if package.digest(plan_bytes) != digest:
        raise ValueError('Rollback expiry found a different installation plan')
    plan = json.loads(plan_bytes)
    if plan['format'] != FORMAT:
        raise ValueError('Unrecognised rollback plan')
    return directory, plan


def expire_one(queue, live):
    """Run under the host coordinator lock, when no installation or recovery is outstanding."""
    from jasmine_web.batch.visualiser_deployments import expired_rollback
    row = expired_rollback(queue)
    if not row or manifest_digest(live) != row['live']:
        return
    keep, latest = read_plan(row['keep_bundle'],row['keep_plan'])
    if keep.name == str(row['bundle']) or latest['after'] != row['live']:
        raise ValueError('Rollback expiry could not identify the immediate predecessor')
    checked_bundle(keep/'bundle',latest['before'])
    directory, plan = read_plan(row['bundle'],row['plan'])
    slot, expired = directory / 'bundle', directory / 'expired-bundle'
    if slot.exists() or slot.is_symlink():
        if expired.exists() or expired.is_symlink():
            raise ValueError('Ambiguous rollback expiry directories')
        checked_bundle(slot, plan['before' if row['state']=='installed' else 'after'])
        slot.rename(expired)
        sync_directory(directory)
    if expired.exists() or expired.is_symlink():
        package.trusted(expired, directory=True)
        # The directory was atomically detached after validation. Retry interrupted removal here.
        shutil.rmtree(expired)
        sync_directory(directory)
    with queue._connection() as c:
        c.execute("""UPDATE visualiser_installations SET rollback_pruned_at=clock_timestamp()
            WHERE pool_id=%s AND bundle=%s AND plan=%s AND state=%s AND rollback_pruned_at IS NULL""",
            (queue.pool_id,row['bundle'],row['plan'],row['state']))
