"""(C) Copyright 2026, by Ross Richardson

Verify retained successful output bytes against trusted completion receipts.
Backups and private chart preparation avoid reparsing unchanged scientific rows.
This never establishes model success and requires snapshot SQL success receipts.
@author ross richardson
"""
from pathlib import Path
import re

from .artifacts import ArtifactError
from .queue_adapter import _catalogue_outputs, _result_configuration


def verify_completed_output(lease, work, receipts):
    """Return a byte-verified catalogue only for every completed planned seed.

    The trusted private caller selects a finished successful attempt and its own
    repetition rows in the same PostgreSQL snapshot. Callers must not provide
    user-supplied hashes. The normal executor remains responsible for original
    scientific CSV validation; exact SHA-256/size/options matching here proves
    that those validated bytes have not changed since successful completion.

    Receipt validation precedes the expensive file reads. A missing/invalid seed
    or hash never falls back to accepting output without a completion receipt.
    """
    if getattr(lease,'phase',None)!='finished':
        raise ArtifactError('Only a finished successful attempt can reuse output receipts')
    configuration=_result_configuration(lease)
    seeds=configuration.as_dict()['seed_plan']['seeds']
    if (not isinstance(receipts,list) or len(receipts)!=len(seeds) or not receipts
            or any(not isinstance(row,dict) or set(row)!={'actual_seed','output_fingerprint'}
                   or row['actual_seed']!=seed or type(row['output_fingerprint']) is not str
                   or not re.fullmatch('[a-f0-9]{64}',row['output_fingerprint'])
                   for row,seed in zip(receipts,seeds))):
        raise ArtifactError('Complete successful output receipts are required for retained output verification')
    actual=_catalogue_outputs(Path(work)/'output',configuration,lease.configuration_id,
                              include_files=True,validate_rows=False)
    if [(row['seed'],row['fingerprint']) for row in actual] != [
            (row['actual_seed'],row['output_fingerprint']) for row in receipts]:
        raise ArtifactError('Retained output differs from its PostgreSQL receipts')
    return actual
