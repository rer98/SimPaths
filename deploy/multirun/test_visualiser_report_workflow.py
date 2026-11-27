"""(C) Copyright 2026, by Ross Richardson
Exercise Visualiser candidate selection, repeat checks and the evidence passed to maintainers.
@author ross richardson
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from .visualiser import report_workflow as workflow


REV = 'a' * 40
FRONTEND = 'c' * 40


class VisualiserReportWorkflowTests(unittest.TestCase):
    def test_scheduled_checks_reuse_only_retained_success_on_the_same_workflow_and_branch(self):
        repo, branch, name = 'project/SimPaths', 'main', 'visualiser-fixture'
        origin = dict(head_branch=branch, id=1)
        artifact = dict(name=name, expired=False, workflow_run=origin)
        run = dict(path=workflow.WORKFLOW, head_branch=branch, head_repository=dict(full_name=repo),
                   event='schedule', conclusion='success')
        for changed_artifact, changed_run in (
                (dict(expired=True), {}), (dict(name=name+'-diagnostics'), {}),
                (dict(workflow_run=None), {}), (dict(workflow_run=dict(origin, head_branch='rehearsal')), {}),
                ({}, dict(head_branch='other')), ({}, dict(event='pull_request')), ({}, dict(path='different.yml')),
                ({}, dict(head_repository=dict(full_name='another/SimPaths'))), ({}, dict(conclusion='failure')),
                ({}, dict(conclusion='cancelled')), ({}, dict(conclusion=None))):
            with self.subTest(artifact=changed_artifact, run=changed_run):
                records = [dict(artifact, **changed_artifact), dict(artifact, workflow_run=dict(origin, id=2))]
                def response(endpoint):
                    if 'artifacts?' in endpoint:
                        return dict(artifacts=records)
                    return dict(run, **changed_run) if endpoint.endswith('/1') else run
                with patch.object(workflow, 'gh_api', side_effect=response):
                    self.assertEqual(workflow.previous_run(repo, branch, name), 2)
                    records.pop()
                    self.assertIsNone(workflow.previous_run(repo, branch, name))

    def test_manual_revision_rechecks_and_changes_to_deployment_inputs_invalidate_reuse(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / 'project'
            upstream = Path(temp) / 'upstream'
            project.mkdir()
            upstream.mkdir()

            def git(repo, *args):
                return subprocess.check_output(['git', '-C', repo, '-c', 'user.name=Fixture',
                                                '-c', 'user.email=fixture@example.invalid', *args])
            for root in (project, upstream):
                git(root, 'init', '-q', '-b', 'main')
            yml = project / workflow.WORKFLOW
            yml.parent.mkdir(parents=True)
            yml.write_text('test workflow')
            deploy = project / 'deploy'
            deploy.mkdir()
            checker = deploy / 'checker.py'
            checker.write_text('initial inputs')
            git(project, 'add', '.')
            git(project, 'commit', '-qm', 'check')
            git(upstream, 'commit', '--allow-empty', '-qm', 'candidate')
            tip = git(upstream, 'rev-parse', 'HEAD').decode().strip()
            with patch.object(workflow, 'MODEL', project), patch.object(workflow, 'SOURCE', str(upstream)), \
                 patch.object(workflow, 'previous_run', return_value=7) as prior:
                scheduled = workflow.select(None, 'project/SimPaths', 'main')
                self.assertEqual((scheduled[0], scheduled[2]), (tip, 7))
                prior.assert_called_once()
                manual = workflow.select(tip, 'project/SimPaths', 'main')
                self.assertEqual(manual, (tip, scheduled[1], None))
                with self.assertRaises(ValueError):workflow.select('', 'project/SimPaths', 'main')
                with self.assertRaises(ValueError):workflow.select('main', 'project/SimPaths', 'main')
                prior.assert_called_once()
                (project / 'README.md').write_text('model documentation')
                git(project, 'add', '.')
                git(project, 'commit', '-qm', 'docs')
                self.assertEqual(workflow.select(tip, 'project/SimPaths', 'main'), manual)
                checker.write_text('updated check')
                git(project, 'add', '.')
                git(project, 'commit', '-qm', 'update')
                self.assertNotEqual(workflow.select(tip, 'project/SimPaths', 'main')[1], manual[1])

    def test_summary_binds_review_to_the_bundle_and_rejects_changed_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build = root/'build'
            build.mkdir()
            def write(path, value):
                data = json.dumps(value).encode()
                path.write_bytes(data)
                return hashlib.sha256(data).hexdigest()
            chart = build/'chart.json'
            manifest = write(build/'build.json', dict(files={chart.name:write(chart, [1, 2])}))
            comparison = write(root/'comparison.json', dict(differences=[]))
            report = dict(source=workflow.SOURCE, revision=REV, reference='d'*40, manifest_sha256=manifest, checks=dict(chromium='passed'),
                          comparison=dict(sha256=comparison, rows=4, local_folder_differences=0, differences_from_release=1))
            def summarise(status):
                write(root/'report.json', dict(report, status=status))
                return workflow.summary(root, REV, 'b'*40, FRONTEND)
            for status in ('checks passed', 'technical review', 'scientific review'):
                text = summarise(status)
                self.assertIn(status, text)
                self.assertIn('released calculator **1**', text)
                self.assertEqual(json.loads((root/'workflow.json').read_text())['frontend'], FRONTEND)
            with self.assertRaises(ValueError):workflow.summary(root, 'd'*40, 'b'*40, FRONTEND)
            extra = build/'unexpected.txt'
            extra.touch()
            with self.assertRaisesRegex(ValueError, 'Unexpected'):summarise('checks passed')
            extra.unlink()
            chart.write_text('different')
            with self.assertRaisesRegex(ValueError, 'Build file changed'):summarise('checks passed')
            write(chart, [1, 2])
            (root/'comparison.json').write_text('different')
            with self.assertRaisesRegex(ValueError, 'Comparison evidence changed'):summarise('checks passed')

    def test_failed_setup_can_report_without_a_build_or_a_comparison(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = dict(source=workflow.SOURCE, revision=REV, reference='d'*40, checks=dict(build='failed'), status='failed')
            (root/'report.json').write_text(json.dumps(report))
            message = workflow.summary(root, REV, 'b'*40, FRONTEND)
            self.assertIn('**failed**', message)
            self.assertIn('`build`', message)
            (root/'report.json').write_text(json.dumps(dict(report, status='checks passed')))
            with self.assertRaises(ValueError):workflow.summary(root, REV, 'b'*40, FRONTEND)
