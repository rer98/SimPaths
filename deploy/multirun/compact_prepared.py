"""(C) Copyright 2026, by Ross Richardson
Create a losslessly compacted revision of a trusted, immutable prepared dataset.
Existing files, model release, provenance and submitted jobs are never rewritten.
Operator tooling only: run under the existing maintenance and backup fences.
@author ross richardson
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .artifacts import (ArtifactError, copy_verified, digest, fingerprint, inventory,
                        write_attribution, write_json)
from .prepared_dataset import (FORMAT, INPUT_FORMAT, check_receipt, check_input_receipt,
                               revision, verify_snapshot)
from .queue_adapter import read_prepared

SOURCE = Path(__file__).resolve().parents[2]/'src/main/java/simpaths/experiment/SimPathsInputCompaction.java'


def run_compactor(candidate):
    """Use the retained model's own H2 classes on the unpublished copy only."""
    with tempfile.TemporaryDirectory(prefix='.compact-tool-', dir=candidate) as temporary:
        classes = Path(temporary)
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C.UTF-8'}
        with (classes/'compile.log').open('wb') as log:
            subprocess.run(['javac', '-proc:none', '-cp', str(candidate/'model.jar'), '-d', str(classes), str(SOURCE)],
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        with (classes/'compact.log').open('wb') as log:
            subprocess.run(['java', '-Xmx512m', '-XX:ActiveProcessorCount=2', '-cp',
                str(classes)+os.pathsep+str(candidate/'model.jar'), 'simpaths.experiment.SimPathsInputCompaction',
                str(candidate/'input/input'), str(classes/'result.json')],
                env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        return json.loads((classes/'result.json').read_text())


def compact_revision(source, output):
    source, output = Path(source).absolute(), Path(output).absolute()
    if (output.exists() or output.is_symlink() or output.is_relative_to(source)
            or any(p.is_symlink() for p in (output, *output.parents))):
        raise ArtifactError('Choose a new private revision directory outside the original dataset')
    original = read_prepared(source)
    kind = original['identity']['format']
    if kind not in (FORMAT, INPUT_FORMAT):
        raise ArtifactError('Only validated production input formats can be compacted')
    verify_snapshot(source, original)
    original_receipt = fingerprint(source/'receipt.json')
    identity = original['identity']
    needed = (sum(v['bytes'] for v in identity['prepared'].values()) + identity['model']['bytes']
              + 2*identity['prepared']['input.mv.db']['bytes'] + (512 << 20))
    if shutil.disk_usage(output.parent).free < needed:
        raise ArtifactError('Insufficient space for a separate compacted revision; original retained')
    staging = Path(tempfile.mkdtemp(prefix='.compact-revision-', dir=output.parent))
    try:
        copy_verified(source/'input', identity['prepared'], staging/'input')
        if fingerprint(source/'model.jar', staging/'model.jar') != identity['model']:
            raise ArtifactError('Retained model changed during copying')
        if 'release' in identity:
            if fingerprint(source/'run.sh', staging/'run.sh') != identity['release']['runner']:
                raise ArtifactError('Retained runner changed during copying')
        proof = run_compactor(staging)
        prepared = inventory(staging/'input')
        if set(prepared) != set(identity['prepared']) or any(
                prepared[name] != item for name, item in identity['prepared'].items() if name != 'input.mv.db'):
            raise ArtifactError('Compaction changed another input file')
        old, new = identity['prepared']['input.mv.db'], prepared['input.mv.db']
        if (proof.get('passed') is not True or proof.get('logical_contents_preserved') is not True
                or proof.get('format_version') != 1 or proof.get('source_database_sha256') != old['sha256']
                or proof.get('database_sha256') != new['sha256'] or proof.get('original_bytes') != old['bytes']
                or proof.get('compacted_bytes') != new['bytes'] or new['bytes'] > old['bytes']):
            raise ArtifactError('Compaction evidence does not match both revisions')
        value = deepcopy(identity)
        value['prepared'] = prepared
        value['input_compaction'] = dict(source_receipt_sha256=original['sha256'], **proof)
        receipt = dict(identity=value, sha256=digest(value),
                       revision=revision(value) if kind == FORMAT else 'inputs-'+digest(value))
        (check_receipt if kind == FORMAT else check_input_receipt)(receipt)
        verify_snapshot(staging, receipt)
        verify_snapshot(source, original)
        if fingerprint(source/'receipt.json') != original_receipt:
            raise ArtifactError('Original receipt changed during compaction')
        write_attribution(staging)
        write_json(staging/'receipt.json', receipt)
        for file in staging.rglob('*'):
            if file.is_file():
                file.chmod(0o400)
                with file.open('rb') as stream: os.fsync(stream.fileno())
        staging.rename(output)
        return receipt
    finally:
        if staging.exists(): shutil.rmtree(staging)


def register_revision(preparations, original_id, output):
    """Publish the verified clone with the original access restrictions intact."""
    receipt = read_prepared(output)
    verify_snapshot(output, receipt)
    proof = receipt['identity'].get('input_compaction')
    if not proof:
        raise ArtifactError('Require a verified compaction revision')
    return preparations.register_revision(original_id,
        expected_fingerprint=proof['source_receipt_sha256'], prepared_fingerprint=receipt['sha256'],
        location=str(Path(output).absolute()), image=receipt['identity']['source_image'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    receipt = compact_revision(args.source, args.output)
    proof = receipt['identity']['input_compaction']
    print(f"Verified new revision: {proof['original_bytes']} -> {proof['compacted_bytes']} bytes. Original retained.")


if __name__ == '__main__': main()
