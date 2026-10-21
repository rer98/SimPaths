"""(C) Copyright 2026, by Ross Richardson

Small, isolated checks for read-only deployment identity collection and verification.
Uses fictional artifacts and temporary Git repositories; no services or models.
@author ross richardson
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from deploy import release_record as tool


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())


def save(path, value):
    put(path, json.dumps(value, sort_keys=True))


def fp(path):
    data = path.read_bytes()
    return {'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()}


def commit(repo):
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', '-C', str(repo),
        'commit', '-m', 'Fictional release fixture'], check=True, capture_output=True)


class ReleaseRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='release-record-check-', dir='/tmp-codex')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repos = {name:self.root/name for name in tool.REPOSITORIES}
        for root in self.repos.values():
            root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
            put(root/'README.md', 'Fictional source fixture\n')
            put(root/'private.env', 'SECRET_CANARY_MUST_NOT_BE_COPIED\n')
        for name in ('simpaths', 'core'):put(self.repos[name]/'pom.xml', '<project/>\n')
        put(self.repos['core']/'license.txt', 'Fictional licence fixture\n')
        put(self.repos['frontend']/'LICENCE', 'Fictional licence fixture\n')
        put(self.repos['frontend']/'requirements-vm.txt', 'example==1.0\n')
        put(self.repos['frontend']/'constraints-runtime.txt', 'example==1.0\n')
        put(self.repos['simpaths']/'deploy/multirun/requirements.txt', 'example==2.0\n')
        put(self.repos['simpaths']/'deploy/multirun/visualiser/package.json', '{"name":"compiler-fixture"}\n')
        put(self.repos['simpaths']/'deploy/multirun/visualiser/package-lock.json', '{"lockfileVersion":3}\n')
        put(self.repos['visualiser']/'package.json', '{"name":"fixture"}\n')
        for name in tool.ADAPTERS:
            put(self.repos['simpaths']/'deploy/multirun/visualiser'/name, '// tiny integration fixture\n')
        for folder, cls, method in (('batch/', 'Queue', 'migrations'), ('', 'PostgresVMState', 'migrate')):
            base = self.repos['frontend']/'jasmine_web'/folder
            put(base/('store.py' if folder else 'vm_state.py'),
                'raise RuntimeError("This app must never be imported")\n'
                f'class {cls}:\n    def {method}(self):\n'
                '        for version, name in enumerate(("001.sql", "002.sql"), 1):\n            pass\n')
            put(base/'001.sql', 'CREATE TABLE fictional (id integer);\n')
            put(base/'002.sql', 'ALTER TABLE fictional ADD label text;\n')
        put(self.repos['visualiser']/'src/App.js', '// upstream fixture\n')
        put(self.repos['visualiser']/'public/logo.svg', '<svg/>\n')
        for root in self.repos.values():commit(root)
        self.image = 'sha256:'+'a'*64
        self.package = self.root/'prepared'
        put(self.package/'model.jar', b'fictional-model-jar')
        put(self.package/'input/training.txt', 'fictional-input\n')
        put(self.package/'profile.json', '{"population":100000}\n')
        self.identity = {'format':tool.PREPARED_FORMATS[0], 'source_image':self.image,
            'model':fp(self.package/'model.jar'), 'prepared':{'training.txt':fp(self.package/'input/training.txt')},
            'profile':fp(self.package/'profile.json')}
        self.receipt = {'identity':self.identity, 'sha256':tool.digest(self.identity), 'revision':'calibration-fixture'}
        save(self.package/'receipt.json', self.receipt)
        self.build = self.root/'visualiser-build'
        put(self.build/'visualiser.js', '// compiled fixture\n')
        self.build_value = {'format':'simpaths.visualiser.build.v1',
            'revision':tool.git_state(self.repos['visualiser'])['commit'], 'mode':'development-paired',
            'transport':'simpaths.visualiser.catalogue.v1', 'dependencies':{'react':'19.2.7'},
            'toolchain':{'node':'v22.23.1'}, 'files':{'visualiser.js':fp(self.build/'visualiser.js')['sha256']},
            'source_hashes':{'App.js':fp(self.repos['visualiser']/'src/App.js')['sha256'],
                             'public/logo.svg':fp(self.repos['visualiser']/'public/logo.svg')['sha256']}}
        save(self.build/'build.json', self.build_value)
        self.catalogue = self.root/'service-state/releases/catalogue.json'
        identity = {'image':self.image, 'model':self.identity['model'], 'defaults':self.identity['prepared']}
        self.release_id = 'release-'+tool.digest(identity)
        self.release_file = self.catalogue.parent/self.release_id/'release.json'
        save(self.release_file, {'format':'simpaths.multirun.release.v1', 'id':self.release_id,
                                'name':'Fictional release', 'identity':identity})
        self.catalogue_value = {'format':'simpaths.multirun.releases.v1', 'default':self.release_id,
            'entries':{self.release_id:{'legacy':False, 'manifest':fp(self.release_file)}}}
        save(self.catalogue, self.catalogue_value)
        self.report = self.root/'finished-proof/report.json'
        save(self.report, {'passed':True, 'cleanup':True, 'model':self.identity['model'], 'image':self.image,
            'prepared_sha256':self.receipt['sha256'], 'private_diagnostics':'SECRET_REPORT_CONTENT'})
        self.selection = {'repositories':{k:str(v) for k,v in self.repos.items()},
            'jar':str(self.package/'model.jar'), 'image':self.image, 'prepared':[str(self.package)],
            'visualiser_build':str(self.build), 'catalogue':str(self.catalogue), 'evidence':[str(self.report)]}
        self.output = self.root/'candidate-record'

    def capture(self):return tool.capture(self.selection, self.output)

    def rejects(self, change):
        change()
        with self.assertRaises((tool.RecordError, OSError)):self.capture()
        self.assertFalse(self.output.exists())

    def test_capture_and_verify_leave_source_refs_indexes_and_files_unchanged(self):
        snapshots = {str(p):p.read_bytes() for root in self.repos.values() for p in root.rglob('*') if p.is_file()}
        record = self.capture()
        self.assertEqual(tool.verify(self.output/'record.json'), record)
        self.assertEqual(snapshots, {str(p):p.read_bytes() for root in self.repos.values()
                                    for p in root.rglob('*') if p.is_file()})
        self.assertFalse(record['inventory']['deployment_approved'])
        self.assertFalse(record['inventory']['installed_host_validated'])
        self.assertEqual(record['simulations_started'], 0)
        self.assertFalse(record['inventory']['prepared'][0]['input_content_hashes_rechecked'])
        self.assertEqual(record['inventory']['migration_declarations']['batch'][1]['version'], 2)
        self.assertIn('deploy/multirun/requirements.txt', record['inventory']['repositories']['simpaths']['dependency_declarations'])
        self.assertTrue(record['inventory']['retained_releases']['entries'][self.release_id]['matches_selected_model'])
        self.assertNotIn('SECRET_', (self.output/'record.json').read_text())
        self.assertNotIn('SECRET_', (self.output/'SUMMARY.md').read_text())

    def test_metadata_only_inputs_cannot_be_mistaken_for_current_content_verification(self):
        path = self.package/'input/training.txt'
        put(path, b'x'*path.stat().st_size)
        original = tool.Reader.file
        def bounded(reader, path, **kwargs):
            self.assertNotIn(self.package/'input', Path(path).parents)
            return original(reader, path, **kwargs)
        with patch.object(tool.Reader, 'file', bounded):record = self.capture()
        prepared = record['inventory']['prepared'][0]
        self.assertFalse(prepared['input_content_hashes_rechecked'])
        self.assertTrue(prepared['calibration_only'])
        self.assertFalse(prepared['production_permissions_verified'])

    def test_changed_jar_breaks_verification(self):
        self.capture();put(self.package/'model.jar', b'changed-model')
        with self.assertRaises(tool.RecordError):tool.verify(self.output/'record.json')

    def test_separate_prepared_model_must_match_selected_jar(self):
        other = self.root/'different-model.jar';put(other, 'another model')
        self.selection['jar'] = str(other)
        self.rejects(lambda:None)

    def test_receipt_digest_cannot_be_bypassed(self):
        self.receipt['identity']['model']['bytes'] += 1
        self.rejects(lambda:save(self.package/'receipt.json', self.receipt))

    def test_prepared_input_sizes_and_unlisted_files_are_rejected(self):
        self.rejects(lambda:put(self.package/'input/training.txt', 'changed input size'))
        put(self.package/'input/training.txt', 'fictional-input\n')
        self.rejects(lambda:put(self.package/'input/unlisted.txt', 'unlisted input'))

    def test_prepared_profile_change_is_rejected(self):
        self.rejects(lambda:put(self.package/'profile.json', '{"population":50000}\n'))

    def test_symlinks_to_private_files_are_rejected(self):
        path = self.package/'input/training.txt';path.unlink()
        self.rejects(lambda:path.symlink_to(self.repos['frontend']/'private.env'))

    def test_visualiser_bundle_changes_and_unlisted_files_are_rejected(self):
        self.rejects(lambda:put(self.build/'visualiser.js', '// changed bundle\n'))
        put(self.build/'visualiser.js', '// compiled fixture\n')
        self.rejects(lambda:put(self.build/'private.env', 'SECRET_'))

    def compiler_metadata(self):
        compiler=self.repos['simpaths']/'deploy/multirun/visualiser'
        self.build_value.update(dependency_lock=dict(package_json_sha256=fp(compiler/'package.json')['sha256'],
            package_lock_sha256=fp(compiler/'package-lock.json')['sha256'],lockfile_version=3,locked_packages=1),
            emitted_packages={'visualiser.js':[dict(name='@fixture/chart',version='1.0.0',path='@fixture/chart')]})
        save(self.build/'build.json',self.build_value)

    def test_compiler_manifest_is_preserved_and_changed_metadata_invalidates_verification(self):
        self.compiler_metadata();record=self.capture()
        self.assertEqual(record['inventory']['visualiser']['identity'],self.build_value)
        self.assertEqual(tool.verify(self.output/'record.json'),record)
        self.build_value['dependency_lock']['locked_packages']=2
        save(self.build/'build.json',self.build_value)
        with self.assertRaises(tool.RecordError):tool.verify(self.output/'record.json')

    def test_incomplete_unknown_or_malformed_compiler_metadata_is_rejected_before_output(self):
        self.compiler_metadata();good=copy.deepcopy(self.build_value)
        cases=[]
        missing=copy.deepcopy(good);missing.pop('emitted_packages');cases.append(missing)
        unknown=copy.deepcopy(good);unknown['unreviewed_field']=True;cases.append(unknown)
        for key,bad in [('package_lock_sha256','invalid'),('locked_packages',True),('lockfile_version',2)]:
            value=copy.deepcopy(good);value['dependency_lock'][key]=bad;cases.append(value)
        unlisted=copy.deepcopy(good);unlisted['emitted_packages']['missing.js']=[];cases.append(unlisted)
        for value in cases:
            with self.subTest(value=value):self.rejects(lambda:save(self.build/'build.json',value))

    def test_emitted_packages_reject_unsafe_paths_duplicates_and_unbounded_lists(self):
        self.compiler_metadata();good=copy.deepcopy(self.build_value)
        package=good['emitted_packages']['visualiser.js'][0]
        bad_lists=[[dict(package,path='../outside')],[dict(package,name='bad name')],
            [package,package],[package]*2001]
        for packages in bad_lists:
            value=copy.deepcopy(good);value['emitted_packages']['visualiser.js']=packages
            with self.subTest(packages=len(packages)):
                self.rejects(lambda:save(self.build/'build.json',value))

    def test_runtime_constraints_and_compiler_locks_are_fingerprinted_even_when_already_dirty(self):
        names=[('frontend','constraints-runtime.txt'),
            ('simpaths','deploy/multirun/visualiser/package.json'),
            ('simpaths','deploy/multirun/visualiser/package-lock.json')]
        for repo,name in names:put(self.repos[repo]/name,'uncommitted first value\n')
        record=self.capture()
        for repo,name in names:
            self.assertEqual(record['inventory']['repositories'][repo]['dependency_declarations'][name],fp(self.repos[repo]/name))
            path=self.repos[repo]/name
            put(path,'uncommitted other value\n')
            with self.subTest(file=name),self.assertRaises(tool.RecordError):tool.verify(self.output/'record.json')
            put(path,'uncommitted first value\n')

    def test_build_revision_and_current_commit_stay_distinct(self):
        put(self.repos['visualiser']/'README.md', 'Later documentation-only commit\n')
        commit(self.repos['visualiser'])
        record = self.capture()
        build = record['inventory']['visualiser']
        self.assertFalse(build['build_revision_matches_checkout'])
        self.assertTrue(build['declared_source_hashes_match_checkout'])
        self.assertNotEqual(build['identity']['revision'], record['inventory']['repositories']['visualiser']['commit'])
        self.assertFalse(record['inventory']['historical_integration_inputs_verified'])

    def test_changed_current_visualiser_source_is_recorded_without_relabeling_old_build(self):
        put(self.repos['visualiser']/'src/App.js', '// changed source\n')
        record = self.capture()
        self.assertFalse(record['inventory']['visualiser']['declared_source_hashes_match_checkout'])
        self.assertTrue(record['inventory']['repositories']['visualiser']['dirty'])
        self.assertIn('Visualiser declared source hashes differ from current checkout.', record['inventory']['outstanding_checks'])

    def test_git_commit_and_status_changes_break_verification(self):
        self.capture();put(self.repos['core']/'README.md', 'Changed source\n')
        with self.assertRaises(tool.RecordError):tool.verify(self.output/'record.json')
        commit(self.repos['core'])
        with self.assertRaises(tool.RecordError):tool.verify(self.output/'record.json')

    def test_dependency_and_migration_changes_break_verification(self):
        self.capture();put(self.repos['frontend']/'requirements-vm.txt', 'example==2.0\n')
        with self.assertRaises(tool.RecordError):tool.verify(self.output/'record.json')
        put(self.repos['frontend']/'requirements-vm.txt', 'example==1.0\n')
        put(self.repos['frontend']/'jasmine_web/batch/001.sql', 'DROP TABLE fictional;\n')
        with self.assertRaises(tool.RecordError):tool.verify(self.output/'record.json')

    def test_catalogue_requires_matching_manifest_and_content_identity(self):
        self.rejects(lambda:put(self.release_file, '{}'))
        self.catalogue_value['entries'][self.release_id]['manifest'] = fp(self.release_file)
        self.rejects(lambda:save(self.catalogue, self.catalogue_value))

    def test_legacy_catalogue_identity_is_preserved(self):
        identity = {'image':self.image, 'model':self.identity['model'], 'defaults':self.identity['prepared']}
        key = 'local-'+identity['model']['sha256'][:16]
        legacy = self.catalogue.parent.parent/'release/release.json';save(legacy, identity)
        self.catalogue_value['entries'][key] = {'legacy':True, 'manifest':fp(legacy)}
        save(self.catalogue, self.catalogue_value)
        self.assertIn(key, self.capture()['inventory']['retained_releases']['entries'])

    def lifecycle_catalogue(self):
        value=copy.deepcopy(self.catalogue_value)
        stamp='2026-10-21T12:00:00+00:00'
        value.update(format='simpaths.multirun.releases.v2',rollback=self.release_id)
        value['entries'][self.release_id].update(availability='available',registered_at=stamp,
            status_changed_at=stamp,unreferenced_since=None)
        return value

    def test_v2_lifecycle_catalogue_is_read_only_and_records_policy(self):
        value=self.lifecycle_catalogue();save(self.catalogue,value)
        original=self.catalogue.read_bytes()
        record=self.capture()
        retained=record['inventory']['retained_releases']
        self.assertEqual(retained['rollback'],self.release_id)
        self.assertEqual(retained['entries'][self.release_id]['availability'],'available')
        self.assertEqual(self.catalogue.read_bytes(),original)
        self.assertEqual(tool.verify(self.output/'record.json'),record)

    def test_invalid_v2_default_rollback_and_timestamps_are_rejected(self):
        for label in ('restricted-default','missing-rollback','naive-time'):
            value=self.lifecycle_catalogue()
            if label=='restricted-default':value['entries'][self.release_id]['availability']='recovery'
            elif label=='missing-rollback':value['rollback']='release-'+'f'*64
            else:value['entries'][self.release_id]['registered_at']='2026-10-21T12:00:00'
            with self.subTest(label=label):self.rejects(lambda:save(self.catalogue,value))

    def test_record_integrity_and_later_evidence_changes_are_detected(self):
        self.capture()
        value = json.loads((self.output/'record.json').read_text())
        value['inventory']['deployment_approved'] = True
        save(self.output/'record.json', value)
        with self.assertRaises(tool.RecordError):tool.verify(self.output/'record.json')
        value['inventory']['deployment_approved'] = False;save(self.output/'record.json', value)
        save(self.report, {'passed':False})
        with self.assertRaises(tool.RecordError):tool.verify(self.output/'record.json')

    def test_partial_or_failed_evidence_does_not_certify_candidate(self):
        save(self.report, {'passed':False, 'cleanup':True})
        record = self.capture()
        self.assertFalse(record['inventory']['historical_reports'][0]['passed'])
        self.assertFalse(record['inventory']['deployment_approved'])
        self.assertTrue(any('lacks a successful' in text for text in record['inventory']['outstanding_checks']))

    def test_capture_never_overwrites_or_writes_inside_repositories_artifacts_or_state(self):
        self.capture();original = (self.output/'record.json').read_bytes()
        with self.assertRaises(tool.RecordError):self.capture()
        self.assertEqual(original, (self.output/'record.json').read_bytes())
        for base in (*self.repos.values(), self.package, self.build, self.catalogue.parent.parent, self.report.parent):
            with self.subTest(base=base), self.assertRaises(tool.RecordError):
                tool.capture(self.selection, base/'new-candidate')
            self.assertFalse((base/'new-candidate').exists())

    def test_output_is_private_even_with_permissive_umask(self):
        previous = os.umask(0)
        try:self.capture()
        finally:os.umask(previous)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        for path in self.output.iterdir():self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_duplicate_nonfinite_and_unexpected_build_metadata_are_rejected(self):
        for text in ('{"format":1,"format":2}', '{"bad":NaN}'):
            self.rejects(lambda:put(self.build/'build.json', text))
        value = copy.deepcopy(self.build_value);value['secret'] = 'SECRET_'
        self.rejects(lambda:save(self.build/'build.json', value))

    def test_mutable_images_traversal_and_logs_are_rejected(self):
        self.selection['image'] = 'latest'
        self.rejects(lambda:None);self.selection['image'] = self.image
        self.build_value['files']['../private.env'] = 'b'*64
        self.rejects(lambda:save(self.build/'build.json', self.build_value))
        self.build_value['files'].pop('../private.env');save(self.build/'build.json', self.build_value)
        self.selection['evidence'] = [str(self.root/'progress.json')]
        self.rejects(lambda:put(self.root/'progress.json', '{}'))

    def test_file_and_total_hash_limits_and_repeated_read_consistency(self):
        jar = self.package/'model.jar'
        with patch.object(tool, 'MAX_HASH_BYTES', jar.stat().st_size-1), self.assertRaises(tool.RecordError):
            tool.Reader().file(jar)
        with self.assertRaises(tool.RecordError):tool.Reader().file(jar, limit=2)
        reader = tool.Reader();first = reader.file(jar);bytes_read = reader.bytes
        self.assertEqual(reader.file(jar), first);self.assertEqual(reader.bytes, bytes_read)
        put(jar, b'x'*jar.stat().st_size)
        with self.assertRaises(tool.RecordError):reader.file(jar)

    def test_changes_during_collection_abort_before_output_creation(self):
        original = tool.evidence
        def changed(path, reader):
            result = original(path, reader)
            put(self.package/'input/training.txt', 'changed metadata')
            return result
        with patch.object(tool, 'evidence', changed):self.rejects(lambda:None)
        put(self.package/'input/training.txt', 'fictional-input\n')
        def changed_git(path, reader):
            result = original(path, reader)
            put(self.repos['core']/'new.txt', 'new source')
            return result
        with patch.object(tool, 'evidence', changed_git):self.rejects(lambda:None)

    def test_read_only_git_disables_optional_locks_and_external_fsmonitor(self):
        original = subprocess.run
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            return original(args, **kwargs)
        with patch.object(tool.subprocess, 'run', run):tool.git_state(self.repos['core'])
        self.assertTrue(calls)
        for args, kwargs in calls:
            self.assertIn('core.fsmonitor=false', args)
            self.assertEqual(kwargs['env']['GIT_OPTIONAL_LOCKS'], '0')
            self.assertEqual(kwargs['env']['GIT_TERMINAL_PROMPT'], '0')
            self.assertEqual(kwargs['timeout'], 15)


if __name__ == '__main__':unittest.main()
