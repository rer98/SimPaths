"""(C) Copyright 2026, by Ross Richardson
Advance one Visualiser installation or recovery under the host update lock.
@author ross richardson
"""
import argparse
from pathlib import Path
import os
import tempfile
import tomllib
from uuid import uuid4

from .. import application_update as app, update_package as package
from ..visualiser_editions import CALCULATOR_FILES
from . import retire_edition, rollback_storage
from .bundle_installation import BundleInstallation, checked_bundle, manifest_digest, verify_assets
from .github_artifact import GitHub, ArtifactError, PendingRun
from .stage_bundle import stage, unpack_download


def check_reference(installed, evidence, installed_digest):
    if evidence.get('reference_manifest') != installed_digest or evidence['reference'] != installed['revision']:
        raise ArtifactError('The comparison did not use the currently installed Visualiser build. Requalify against the installed release.')


def check_calculator(installed, candidate, evidence):
    def calculation(build):
        return (build['mode'],build['toolchain']['node'],{k:build['files'][k] for k in CALCULATOR_FILES})
    if calculation(installed) != calculation(candidate) and evidence['outcome'] == 'checks passed':
        raise ArtifactError('Changing the installed calculator requires a reviewed qualification.')


def settle(queue, emails, entry):
    from jasmine_web.batch import visualiser_deployments as ledger
    if entry['host_state']=='complete':
        state,note = 'installed','Installation verified on the server.'
    elif entry['host_reason']=='verified_rollback':
        state,note = 'rolled_back','Installation failed. The server restored and verified the previous Visualiser.'
    elif entry['host_reason']=='rollback_failed':
        state,note = 'recovery_required',f"Installation and automatic rollback failed. MultiRun admissions remain paused. Operator recovery reference: {entry['update_id']}."
    elif entry['host_state']=='cancelled':
        state,note = 'cancelled','Installation was cancelled before publication.'
    else:
        return False
    ledger.finish(queue,emails,entry,state,note)
    return True


def advance(options,queue,emails,entry):
    from jasmine_web.batch import update_admission as control, visualiser_deployments as ledger
    from jasmine_web.batch.policy import Conflict
    if settle(queue,emails,entry):
        return
    ref, plan = str(entry['update_id']),entry['plan']
    source = package.ROOT/'visualiser-updates'/str(entry['bundle'])
    if (entry['host_state'] in ('draining','pending') and
            (entry['current_revision']!=entry['revision'] or entry['review_state'] not in ('approved','qualified'))):
        control.cancel(queue,ref,plan)
        settle(queue,emails,ledger.outstanding(queue))
        return
    try:
        installer = BundleInstallation(options,queue,source,plan)
    except (ValueError, OSError) as error:
        if entry['host_state'] in ('pending','draining'):
            control.cancel(queue,ref,plan)
            ledger.finish(queue,emails,entry,'failed','The staged Visualiser installation could not be verified before publication.')
        else:
            control.fail(queue,ref,plan,reason='rollback_failed')
            settle(queue,emails,ledger.outstanding(queue))
        raise RuntimeError('The host could not open the staged Visualiser transaction.') from error
    try:
        if entry['host_state'] in ('pending','draining'):
            control.mark_draining(queue,ref,plan)
            if not installer.ready():
                return
            try:
                control.begin_install(queue,ref,plan,before_start=lambda c:ledger.mark_installing(c,queue,entry))
            except Conflict:
                # Reservations or a decision changed. The next tick rereads both.
                return
            app.apply_started(queue,ref,plan,installer.guarded('install',installer.install),
                              installer.guarded('verify',installer.verify),installer.guarded('rollback',installer.rollback),control)
        elif entry['host_state'] in ('installing','failed'):
            if not installer.ready():
                return
            try:
                receipt = installer.restore_and_verify()
            except BlockingIOError:
                return
            except Exception:
                control.fail(queue,ref,plan,reason='rollback_failed')
                raise
            control.recover(queue,ref,plan,receipt)
    finally:
        installer.release_fence()
        latest = ledger.outstanding(queue)
        if latest:
            settle(queue,emails,latest)


def tick(options,queue,github,retirement_token=None):
    from jasmine_web.batch import update_admission as control, visualiser_deployments as ledger
    emails = options.administration['emails'] if options.administration['enabled'] else []
    entry = ledger.outstanding(queue)
    if entry:
        advance(options,queue,emails,entry)
        return
    if not emails:
        return
    review = ledger.next_review(queue,github.repository)
    with queue._connection() as c:
        if c.execute("SELECT 1 FROM deployment_updates WHERE pool_id=%s AND state=ANY(%s::text[])",
                     (queue.pool_id,list(control.ACTIVE))).fetchone():
            return
    live = Path(options.visualiser_build)
    retire_edition.advance(queue,live,github,retirement_token,allow_publish=not review)
    if not review:
        rollback_storage.expire_one(queue,live)
        return
    evidence = review['evidence']
    bundle = str(uuid4())
    try:
        before = manifest_digest(live)
        installed = checked_bundle(live,before)
    except (ValueError,KeyError,OSError):
        ledger.record(queue,emails,review,bundle,state='failed',note='The installed Visualiser files could not be verified. Inspect the installed manifest before retrying.')
        return
    previous = None
    if before != evidence['manifest'] and installed['revision']==evidence['visualiser']:
        previous = ledger.installed_evidence(queue,github.repository,before)
    plan = None
    try:
        if before != evidence['manifest']:
            check_reference(installed,evidence,before)
        identifier, digest = github.locate(evidence,installed['revision'])
        if previous:
            github.check_integration(previous['evidence'],evidence)
        with tempfile.TemporaryDirectory(dir=package.trusted(package.ROOT/'visualiser-updates',directory=True)) as tmp:
            archive = Path(tmp)/'download.zip'
            github.download(identifier,archive)
            if before == evidence['manifest']:
                unpack_download(archive,digest,evidence,Path(tmp),Path(tmp)/'verified')
                verify_assets(options,installed)
            else:
                source,plan = stage(archive,digest,evidence,before,bundle)
                candidate = checked_bundle(source/'bundle',evidence['manifest'])
                check_calculator(installed,candidate,evidence)
                retire_edition.check_candidate(queue,evidence['project'],installed,candidate)
    except PendingRun:
        return
    except Exception as error:
        note = str(error) if isinstance(error,ArtifactError) else 'The worker could not verify the qualification artifact or served assets. Check the workflow evidence and rerun qualification.'
        ledger.record(queue,emails,review,bundle,state='failed',note=note,plan=plan)
        return
    if plan is None:
        ledger.record(queue,emails,review,bundle,state='installed',note='The reviewed build was already installed and verified.')
    else:
        ledger.record(queue,emails,review,bundle,state='draining',note='Waiting for current work and scheduled backups before installation.',plan=plan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retry-rollback',metavar='UPDATE_ID',help='Retry verified recovery of this failed installation')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        with app.coordinator_lock():
            options,queue = app.configured_queue(Path('/etc/simpaths-online/multirun.toml'))
            if args.retry_rollback:
                from jasmine_web.batch import update_admission as control, visualiser_deployments as ledger
                entry = ledger.outstanding(queue)
                if (not entry or str(entry['update_id']) != args.retry_rollback or entry['host_reason']!='rollback_failed'):
                    raise ValueError('The reference must match the current failed Visualiser rollback')
                path = package.ROOT/'visualiser-updates'/str(entry['bundle'])
                installer = BundleInstallation(options,queue,path,entry['plan'])
                try:
                    if not installer.ready():
                        raise RuntimeError('A backup is running. Retry recovery after it finishes.')
                    proof = installer.restore_and_verify()
                    control.recover(queue,args.retry_rollback,entry['plan'],proof)
                    settle(queue,options.administration['emails'],ledger.outstanding(queue))
                finally:
                    installer.release_fence()
                return
            config = tomllib.loads(package.trusted(Path('/etc/simpaths-online/visualiser-updater.toml')).read_text())
            credentials = os.environ.get('CREDENTIALS_DIRECTORY')
            reader = Path(credentials)/'github-token' if credentials else None
            token = reader.read_text().strip() if reader and reader.is_file() else ''
            writer = Path(credentials)/'github-retirement-token' if credentials else None
            tick(options,queue,GitHub(config['repository'],config['branch'],token),
                 writer.read_text().strip() if writer and writer.is_file() else None)
    except BlockingIOError:
        # The timer will retry after the current coordinator or backup releases its lock.
        if args.retry_rollback:
            raise RuntimeError('The coordinator or backup is busy. Retry the same recovery reference.') from None
        return


if __name__ == '__main__':
    main()
