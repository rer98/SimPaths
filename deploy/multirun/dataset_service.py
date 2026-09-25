"""(C) Copyright 2026, by Ross Richardson

Bridge authenticated opaque platform uploads to model-owned input preparation.
The service is an internal API, not a browser endpoint or an email authenticator.

@author ross richardson
"""
from pathlib import Path

from .artifacts import ArtifactError
from .prepare_inputs import prepare
from .prepared_dataset import check_input_receipt, verify_snapshot


def prepare_owned(service, owner, upload_ids, *, defaults, request, jar, image, output, frontend):
    selected = service.resolve_uploads(owner, upload_ids)
    receipt = prepare(defaults, {k:v['path'] for k,v in selected.items()}, request,
                      jar, image, output, frontend)
    expected = {k:{'bytes':v['bytes'],'sha256':v['sha256']} for k,v in selected.items()}
    if receipt['identity']['sources']['uploads'] != expected:
        raise ArtifactError('Upload contents changed before preparation')
    # Resolve again for current approval, ownership and content, then publish.
    # publish repeats approval inside its transaction to close a revocation race.
    service.resolve_uploads(owner, upload_ids)
    dataset = service.publish(owner, receipt['sha256'], uploads=upload_ids)
    return dict(dataset_id=dataset, prepared=Path(output), receipt=receipt)


def publish_provider(service, prepared):
    """Maintainer-only import of a freshly validated provider input revision.

    This API cannot be exposed to a user as an origin/download-policy choice.
    The administrator grants execution access separately after registration.
    """
    from .queue_adapter import read_prepared
    receipt = check_input_receipt(read_prepared(prepared))
    verify_snapshot(prepared, receipt)
    return service.register_provider(receipt['sha256'])
