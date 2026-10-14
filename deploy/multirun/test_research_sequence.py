"""(C) Copyright 2026, by Ross Richardson

Small evidence fixtures verify sequential launch, archival and failure guards.
No models, Docker, databases or native CSV audits are started by these checks.
@author ross richardson
"""
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from deploy.multirun import research_benchmarks as bench
from deploy.multirun import research_sequence as sequence
from deploy.multirun.artifacts import ArtifactError, fingerprint
from deploy.multirun import test_research_benchmarks as fixtures


class SequenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prepared = self.root/'prepared';self.prepared.mkdir()
        (self.prepared/'model.jar').write_bytes(b'fake-model')
        (self.prepared/'profile.json').write_text('{}')
        self.model = fingerprint(self.prepared/'model.jar')
        self.publish(self.prepared/'receipt.json', dict(sha256='c'*64,
            identity=dict(model=self.model, source_image='sha256:'+'b'*64)))
        self.frontend = self.root/'frontend';(self.frontend/'scripts').mkdir(parents=True)
        (self.frontend/'scripts/test_batch_queue.py').write_text('# fictional; never executed\n')
        (self.frontend/'requirements-batch-web.txt').write_text('# fictional\n')
        self.addCleanup(patch.stopall)
        patch.dict(sequence.COMMON, end_year=2020).start()
        self.events = [];self.audits = {}
        patch.object(sequence, 'resources', return_value={'fixture_preflight': True}).start()

    def publish(self, path, value):
        path.write_text(json.dumps(value, sort_keys=True)+'\n')

    def arguments(self, **changes):
        value = dict(frontend=self.frontend, prepared=self.prepared, output=self.root/'sequence',
                     cases=['B', 'A'], reference_case=[], pause_seconds=0, dry_run=False)
        value.update(changes)
        return Namespace(**value)

    def fixture(self, label, directory, *, person='same'):
        # Reuse the existing tiny native-completion fixture and its real archive
        # validation/disposal. Only the simulated launch and annual audit are stubbed.
        source = fixtures.BenchmarkTests('runTest');source.root = directory.parent
        root, audit = source.fixture(label, person=person)
        record_path = root/'model-proof/report.json'
        record = json.loads(record_path.read_text())
        record.update(model=self.model, source_sha256={name:fingerprint(sequence.ROOT/name)['sha256']
                      for name in record['source_sha256']})
        self.publish(record_path, record)
        evidence, _ = bench.completion(root)
        audited = json.loads(audit.read_text());details = audited['sources'][0]
        details.update(provenance={Path(name).name:evidence[name]['fingerprint'] for name in bench.EVIDENCE[1:]},
                       outer_report=evidence['report.json']['fingerprint'])
        self.publish(audit, audited)
        self.audits[label] = audited
        return root, audit

    def fake_command(self, command, log, env, *, fail=None, changed_person=None, change_source=False):
        script = Path(command[1]).name
        if script == 'research_calibration.py':
            stage = 'run';directory = Path(command[command.index('--output')+1]);label = directory.name
            if self.events:
                previous = self.events[-1][0]
                self.assertEqual(self.events[-1][1], 'dispose')
                self.assertEqual(bench.output_files(directory.parent/previous/'model-proof/retained-output'), set())
            self.fixture(label, directory, person='different content' if changed_person == label else 'same')
        elif script == 'research_storage_audit.py':
            stage = 'audit';directory = Path(command[command.index('--output')+1]);label = directory.name
            directory.mkdir();self.publish(directory/'report.json', self.audits[label])
        else:
            stage = command[2]
            if stage == 'compare':
                label = log.name.split('-')[0]
            else:
                label = Path(command[command.index('--completed')+1]).name
        self.events.append((label, stage))
        log.write_text('fictional '+stage+'\n')
        self.assertEqual(env['TMPDIR'], str(self.root/'sequence/trials'))
        self.assertEqual(env['PIP_NO_INPUT'], '1')
        if fail == (label, stage):raise ArtifactError('Fictional failure at '+stage)
        if script == 'research_benchmarks.py':
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = bench.main(command[2:])
            if code:raise ArtifactError('Real evidence tool rejected the fixture')
            if change_source and stage == 'dispose':
                (self.frontend/'scripts/test_batch_queue.py').write_text('# changed\n')

    def run_plan(self, plan, **behaviour):
        with redirect_stdout(io.StringIO()):
            return sequence.execute(plan, runner=lambda *args:self.fake_command(*args, **behaviour))

    def test_second_model_starts_only_after_first_is_audited_archived_compared_and_disposed(self):
        plan = sequence.make_plan(self.arguments())
        report = self.run_plan(plan)
        self.assertTrue(report['passed'])
        self.assertEqual(self.events, [(label, stage) for label in ('B', 'A')
                                     for stage in ('run', 'audit', 'archive', 'compare', 'dispose')])
        for label in ('B', 'A'):
            directory = self.root/'sequence/trials'/label
            self.assertTrue((directory/'model-proof/report.json').is_file())
            self.assertTrue((directory/'model-proof/output-manifest.json').is_file())
            self.assertEqual(bench.output_files(directory/'model-proof/retained-output'), set())
            bench.read_archive(self.root/'sequence/archives'/label)
        self.assertEqual((self.root/'sequence').stat().st_mode & 0o777, 0o700)

    def test_cpu_sequence_freezes_distinct_quotas_and_reclaims_each_case_before_the_next(self):
        plan = sequence.make_plan(self.arguments(cases=['A','D','E']))
        self.assertEqual(plan['comparison_family'], 'cpu')
        self.assertEqual(plan['limits']['cpu_millis_by_case'], dict(A=2000,D=1500,E=1000))
        for case in plan['cases']:
            settings = case['settings'];command = case['command']
            self.assertEqual(int(command[command.index('--cpu-millis')+1]), bench.CASE_CPUS[case['label']])
            self.assertEqual((settings['heap_mib'],settings['memory_mib'],settings['max_memory_mib']),
                             (4096,5120,5120))
            self.assertEqual(settings['max_attempts'], 1)
        result = self.run_plan(plan)
        self.assertTrue(result['passed'])
        self.assertEqual(self.events, [(label,stage) for label in ('A','D','E')
                                     for stage in ('run','audit','archive','compare','dispose')])
        comparison = json.loads((self.root/'sequence/comparisons/03-after-E/report.json').read_text())
        self.assertTrue(comparison['complete'])
        self.assertEqual(list(comparison['cases']), ['A','D','E'])

    def test_failed_model_audit_or_disposal_never_starts_second_case(self):
        plan = sequence.make_plan(self.arguments())
        for stage in ('run', 'audit', 'dispose'):
            with self.subTest(stage=stage):
                # A separate new workspace is needed for each interrupted sequence.
                with tempfile.TemporaryDirectory() as temp:
                    original = self.root;self.root = Path(temp)
                    alternate = dict(plan, output=str(self.root/'sequence'), cases=[
                        dict(c, completed=str(self.root/'sequence/trials'/c['label']),
                             command=c['command'][:-1]+[str(self.root/'sequence/trials'/c['label'])],
                             audit=str(self.root/'sequence/audits'/c['label']),
                             archive=str(self.root/'sequence/archives'/c['label'])) for c in plan['cases']])
                    self.events = []
                    try:
                        with self.assertRaises(ArtifactError):self.run_plan(alternate, fail=('B', stage))
                        report = json.loads((self.root/'sequence/report.json').read_text())
                        self.assertFalse(report['passed'])
                        self.assertFalse(any(label == 'A' for label, _ in self.events))
                        self.assertTrue(bench.output_files(self.root/'sequence/trials/B/model-proof/retained-output'))
                    finally:self.root = original

    def test_changed_annual_output_stops_before_disposing_second_case(self):
        plan = sequence.make_plan(self.arguments())
        with self.assertRaises(ArtifactError):self.run_plan(plan, changed_person='A')
        self.assertEqual(self.events[-1], ('A', 'compare'))
        self.assertTrue(bench.output_files(self.root/'sequence/trials/A/model-proof/retained-output'))
        self.assertTrue((self.root/'sequence/archives/A/archive.json').exists())

    def test_changed_driver_between_cases_stops_before_another_launch(self):
        plan = sequence.make_plan(self.arguments())
        with self.assertRaisesRegex(ArtifactError, 'sources changed'):
            self.run_plan(plan, change_source=True)
        self.assertEqual(self.events[-1], ('B', 'dispose'))
        self.assertFalse((self.root/'sequence/trials/A').exists())

    def test_existing_c_reference_is_compared_without_launch_or_deletion(self):
        native, audit = self.fixture('C', self.root/'C')
        archive = self.root/'C-archive';bench.archive(native, audit, archive)
        before = {name:fingerprint(native/name) for name in bench.EVIDENCE}
        files = bench.output_files(native/'model-proof/retained-output')
        plan = sequence.make_plan(self.arguments(reference_case=['C='+str(archive)]))
        self.run_plan(plan)
        self.assertFalse(any(label == 'C' for label, _ in self.events))
        comparison = json.loads((self.root/'sequence/comparisons/02-after-A/report.json').read_text())
        self.assertTrue(comparison['complete'])
        self.assertEqual({name:fingerprint(native/name) for name in bench.EVIDENCE}, before)
        self.assertEqual(bench.output_files(native/'model-proof/retained-output'), files)

    def test_wrong_reference_or_changed_prepared_model_is_rejected_before_writes(self):
        native, _ = self.fixture('C', self.root/'C')
        self.publish(native/'model-proof/report.json', dict(json.loads(
            (native/'model-proof/report.json').read_text()), prepared_sha256='0'*64))
        with self.assertRaises(ArtifactError):sequence.make_plan(self.arguments(reference_case=['C='+str(native)]))
        (self.prepared/'model.jar').write_bytes(b'changed-model')
        with self.assertRaises(ArtifactError):sequence.make_plan(self.arguments())
        self.assertFalse((self.root/'sequence').exists())

    def test_duplicate_cases_and_existing_destination_are_rejected(self):
        with self.assertRaises(ArtifactError):sequence.make_plan(self.arguments(cases=['B', 'B']))
        (self.root/'sequence').mkdir()
        with self.assertRaises(ArtifactError):sequence.make_plan(self.arguments())

    def test_insufficient_capacity_stops_before_creating_evidence_or_running_commands(self):
        plan = sequence.make_plan(self.arguments())
        with patch.object(sequence, 'resources', side_effect=ArtifactError('No capacity')):
            with self.assertRaises(ArtifactError):self.run_plan(plan)
        self.assertFalse(self.events)
        self.assertFalse((self.root/'sequence').exists())

    def test_sequence_lock_excludes_another_runner_and_rejects_a_symlink(self):
        with sequence.exclusive(self.root):
            with self.assertRaises(BlockingIOError):
                with sequence.exclusive(self.root):self.fail('Concurrent runner acquired the lock')
        lock = self.root/'.simpaths-research-sequence.lock';lock.unlink()
        lock.symlink_to(self.prepared/'model.jar')
        with self.assertRaises(OSError):
            with sequence.exclusive(self.root):self.fail('Symlink lock was accepted')

    def test_real_small_subprocess_preserves_stdout_stderr_and_reports_nonzero_exit(self):
        for code in (0, 3):
            with self.subTest(code=code):
                log = self.root/f'child-{code}.log';terminal = io.StringIO()
                command = [sys.executable, '-c',
                           'import sys; print("ordinary progress", flush=True); '
                           f'print("fictional diagnostic", file=sys.stderr); sys.exit({code})']
                with redirect_stdout(terminal):
                    if code:
                        with self.assertRaisesRegex(ArtifactError, 'exited 3'):
                            sequence.run_command(command, log, {})
                    else:sequence.run_command(command, log, {})
                self.assertIn('ordinary progress', log.read_text())
                self.assertIn('fictional diagnostic', log.read_text())
                self.assertEqual(log.read_text(), terminal.getvalue())
                self.assertEqual(log.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':unittest.main()
