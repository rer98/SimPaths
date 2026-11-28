"""(C) Copyright 2026, by Ross Richardson
Exercise Visualiser publication and crash recovery with real directories and a fictional host.
@author ross richardson
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4
from zipfile import ZipFile

from . import update_package as package
from .test_update_package import Host, Queue
from .visualiser import bundle_installation as bundle, install_worker, rollback_storage, stage_bundle


class BundleTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.source = root / 'visualiser-updates' / str(uuid4())
        self.slot = self.source / 'bundle'
        self.slot.mkdir(parents=True)
        self.live = root / 'visualiser-installed'
        self.live.mkdir()
        self.options = SimpleNamespace(visualiser_build=self.live, state=root, origin='https://test.invalid')
        self.queue = Queue()
        self.host = Host()
        stack.enter_context(patch.object(package, 'ROOT', root))
        stack.enter_context(patch.object(package, 'command', self.host.command))
        stack.enter_context(patch.object(package, 'settings_snapshot', return_value={}))
        stack.enter_context(patch.object(os, 'chown'))  # ordinary user in a private temporary directory
        def trusted(path, *, directory=False):
            path = Path(path)
            self.assertTrue(path.is_relative_to(root))
            if path.is_symlink() or not (path.is_dir() if directory else path.is_file()):
                raise ValueError('Expected a real fixture entry')
            return path
        stack.enter_context(patch.object(package, 'trusted', trusted))
        stack.enter_context(patch.object(bundle.BundleInstallation, 'backup_fence', autospec=True))
        self.http = stack.enter_context(patch.object(bundle.subprocess, 'check_output', side_effect=self.serve))
        self.before = self.write_bundle(self.live, b'old', '1.visualiser.js')
        self.after = self.write_bundle(self.slot, b'new', '2.visualiser.js')
        self.plan = self.write_plan()

    def serve(self, command, **kwargs):
        url = command[-1]
        self.assertTrue(url.startswith('https://test.invalid/visualiser'))
        name = 'index.html' if '/visualiser/' in url else url.rsplit('/', 1)[1]
        return (self.live / name).read_bytes()

    def write_bundle(self, directory, label, chunk):
        files = {}
        for name in bundle.REQUIRED | {chunk}:
            data = name.encode() if name.endswith('.cjs') else label + name.encode()
            (directory / name).write_bytes(data)
            files[name] = package.digest(data)
        body = json.dumps(dict(format='simpaths.visualiser.build.v1', mode='development-paired',
                               revision='b'*40, files=files, toolchain=dict(node='v22.23.1'))).encode()
        (directory / 'build.json').write_bytes(body)
        return package.digest(body)

    def write_plan(self):
        body = json.dumps(dict(format=bundle.FORMAT, before=self.before, after=self.after)).encode()
        (self.source / 'plan.json').write_bytes(body)
        return package.digest(body)

    def installation(self):
        return bundle.BundleInstallation(self.options, self.queue, self.source, self.plan)

    def assert_active(self, digest, chunk):
        self.assertEqual(bundle.manifest_digest(self.live), digest)
        self.assertEqual([p.name for p in self.live.glob('*.visualiser.js')], [chunk])
        self.assertTrue(all(self.host.states[unit] == 'active' for unit in (package.PRIVATE, package.PUBLIC)))

    def test_install_and_restore_whole_directories_under_the_normal_service_sequence(self):
        installer = self.installation()
        installer.install()
        self.assertEqual(len(installer.verify()), 64)
        self.assert_active(self.after, '2.visualiser.js')
        self.assertEqual(len(self.installation().restore_and_verify()), 64)
        self.assert_active(self.before, '1.visualiser.js')
        self.installation().restore_and_verify()
        self.assert_active(self.before, '1.visualiser.js')
        urls = {call.args[0][-1] for call in self.http.call_args_list}
        for chunk in ('1.visualiser.js', '2.visualiser.js'):
            self.assertIn('https://test.invalid/visualiser-assets/' + chunk, urls)

    def test_expiry_validates_the_predecessor_and_resumes_after_partial_removal(self):
        from jasmine_web.batch import visualiser_deployments as ledger
        import shutil
        self.installation().install()
        keep=self.source.with_name(str(uuid4()))
        shutil.copytree(self.source,keep)
        row = dict(plan=self.plan,bundle=self.source.name,state='installed',live='c'*64,
                   keep_bundle=keep.name,keep_plan=self.plan)
        with patch.object(ledger,'expired_rollback',return_value=row), patch.object(self.queue,'_connection') as db:
            rollback_storage.expire_one(self.queue,self.live)
            self.assertTrue(self.slot.exists())
            row['live']=self.after
            kept=keep/'bundle'/'index.html'
            body=kept.read_bytes()
            kept.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'asset differs'):
                rollback_storage.expire_one(self.queue,self.live)
            self.assertTrue(self.slot.exists())
            kept.write_bytes(body)
            plan = self.source/'plan.json'
            original=plan.read_bytes()
            plan.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'different installation plan'):
                rollback_storage.expire_one(self.queue,self.live)
            plan.write_bytes(original)
            with patch.object(rollback_storage.shutil,'rmtree',side_effect=OSError('interrupted')):
                with self.assertRaises(OSError):
                    rollback_storage.expire_one(self.queue,self.live)
            db.assert_not_called()
            self.assertFalse(self.slot.exists())
            expired = self.source/'expired-bundle'
            (expired/'index.html').unlink()
            rollback_storage.expire_one(self.queue,self.live)
            self.assertFalse(expired.exists())
            self.assertEqual(plan.read_bytes(),original)
            self.assertTrue((self.source/'install-progress.json').exists())
            self.assert_active(self.after,'2.visualiser.js')
            # A restart after deletion but before writing the database receipt is safe.
            rollback_storage.expire_one(self.queue,self.live)
            self.assertEqual(db.call_count,2)

    def test_serving_previous_javascript_fails_verification_and_recovery_restores_it(self):
        installer = self.installation()
        installer.install()
        def stale(command, **kwargs):
            if command[-1].endswith('/visualiser.js'):
                return (self.slot / 'visualiser.js').read_bytes()
            return self.serve(command)
        self.http.side_effect = stale
        with self.assertRaisesRegex(ValueError, 'HTTPS served a different'):
            installer.verify()
        self.http.side_effect = self.serve
        self.installation().restore_and_verify()
        self.assert_active(self.before, '1.visualiser.js')

    def test_recovery_after_services_stop_before_publication_keeps_original(self):
        installer = self.installation()
        with patch.object(installer, 'publish_programs', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                installer.install()
        self.installation().restore_and_verify()
        self.assert_active(self.before, '1.visualiser.js')

    def test_changed_asset_or_stale_installed_manifest_refused_before_service_stop(self):
        (self.slot / 'index.html').write_text('unexpected content')
        with self.assertRaisesRegex(ValueError, 'asset differs'):
            self.installation().install()
        self.before = '9' * 64
        self.plan = self.write_plan()
        with self.assertRaisesRegex(ValueError, 'manifest differs'):
            self.installation().install()
        self.assertFalse(any(c[:2] == ('systemctl', 'stop') for c in self.host.calls))

    def test_retained_results_block_incompatible_calculator_before_publication(self):
        path = self.slot / 'build.json'
        manifest = json.loads(path.read_bytes())
        data = b'different calculation'
        (self.slot / 'calculation.cjs').write_bytes(data)
        manifest['files']['calculation.cjs'] = package.digest(data)
        path.write_text(json.dumps(manifest))
        self.after = package.digest(path.read_bytes())
        self.plan = self.write_plan()
        db = MagicMock()
        db.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = {'sources': 1}
        with patch.object(self.queue, '_connection', db):
            with self.assertRaises(package.IncompatibleVisualiserUpdate):
                self.installation().install()
        self.assert_active(self.before, '1.visualiser.js')

    def test_unlisted_assets_and_mismatched_plan_rejected(self):
        (self.slot / 'unlisted').touch()
        with self.assertRaisesRegex(ValueError, 'Unexpected or missing file'):
            self.installation().install()
        self.plan = '9' * 64
        with self.assertRaisesRegex(ValueError, 'plan differs'):
            self.installation()

    def artifact(self, *, outcome='checks passed', extra=False, corrupt=False, differences=None):
        archive = self.live.parent / 'download.zip'
        evidence = dict(project='a'*40, visualiser='b'*40, frontend='c'*40, reference='d'*40,
                        outcome=outcome, rows=10, changed=0, manifest=self.after)
        workflow = dict(format='simpaths.visualiser.workflow.v1', simpaths=evidence['project'],
                        frontend=evidence['frontend'], visualiser=evidence['visualiser'],
                        manifest_sha256=evidence['manifest'])
        detail = {} if differences is None else dict(release_differences=differences)
        report = dict(source=stage_bundle.SOURCE, revision=evidence['visualiser'], reference=evidence['reference'],
                      status=outcome, manifest_sha256=self.after, checks={'chromium': 'passed'},
                      comparison=dict(rows=10, differences_from_release=0, local_folder_differences=0,
                                      sha256=package.digest(json.dumps(detail).encode())))
        if differences is not None:
            evidence.update(changed=len(differences),row_changes=stage_bundle.row_changes(differences))
            report['comparison'].update(differences_from_release=evidence['changed'],row_changes=evidence['row_changes'])
        with ZipFile(archive, 'w') as output:
            for name, body in [('workflow.json', workflow), ('report.json', report), ('comparison.json', detail)]:
                output.writestr(name, json.dumps(body))
            for path in self.slot.iterdir():
                body = b'wrong asset' if corrupt and path.name == 'visualiser.js' else path.read_bytes()
                output.writestr('build/' + path.name, body)
            output.writestr('logs/qualification.log', 'diagnostics only')
            if extra:
                output.writestr('build/unlisted.js', b'unexpected')
        return archive, package.digest(archive.read_bytes()), evidence

    def test_stage_verified_artifact_then_install_and_recover(self):
        archive, digest, evidence = self.artifact()
        reference = str(uuid4())
        source, plan = stage_bundle.stage(archive, digest, evidence, self.before, reference)
        self.assertEqual((source, plan), stage_bundle.stage(archive, digest, evidence, self.before, reference))
        self.assertEqual({p.name for p in source.iterdir()}, {'plan.json', 'bundle'})
        installer = bundle.BundleInstallation(self.options, self.queue, source, plan)
        installer.install()
        installer.verify()
        installer.rollback()
        self.assert_active(self.before, '1.visualiser.js')

    def test_worker_rechecks_an_already_installed_build_without_staging(self):
        from jasmine_web.batch import visualiser_deployments
        archive, digest, evidence = self.artifact()
        installer = self.installation()
        installer.install()
        review = dict(evidence=evidence)
        github = MagicMock()
        github.locate.return_value = (123, digest)
        github.download.side_effect = lambda _, destination: destination.write_bytes(archive.read_bytes())
        self.options.administration = dict(enabled=True, emails=['admin@example.org'])
        with patch.object(visualiser_deployments,'outstanding',return_value=None), \
                patch.object(visualiser_deployments,'next_review',return_value=review), \
                patch.object(self.queue,'_connection') as connection, \
                patch.object(visualiser_deployments,'record') as record:
            connection.return_value.__enter__.return_value.execute.return_value.fetchone.return_value=None
            install_worker.tick(self.options,self.queue,github)
            self.assertEqual(record.call_args.kwargs['state'],'installed')
        self.assert_active(self.after, '2.visualiser.js')

    def test_artifact_rejections_leave_no_partial_transaction(self):
        reference = str(uuid4())
        for problem in ('failed', 'extra', 'changed asset', 'wrong run content', 'download digest'):
            with self.subTest(problem=problem):
                archive, digest, evidence = self.artifact(outcome='failed' if problem == 'failed' else 'checks passed',
                                                          extra=problem == 'extra', corrupt=problem == 'changed asset')
                if problem == 'wrong run content':
                    evidence['project'] = '9'*40
                if problem == 'download digest':
                    digest = '9'*64
                with self.assertRaises(ValueError):
                    stage_bundle.stage(archive, digest, evidence, self.before, reference)
                self.assertFalse((self.source.parent / reference).exists())
                self.assert_active(self.before, '1.visualiser.js')

    def test_staged_row_counts_must_match_the_actual_comparison(self):
        archive,digest,evidence = self.artifact(outcome='scientific review',differences=[
            dict(before={'year':2019},after=None)])
        reference = str(uuid4())
        wrong = dict(evidence,row_changes=dict(added=1,removed=0,modified=0))
        with self.assertRaisesRegex(ValueError,'Chart row changes'):
            stage_bundle.stage(archive,digest,wrong,self.before,reference)
        stage_bundle.stage(archive,digest,evidence,self.before,reference)
