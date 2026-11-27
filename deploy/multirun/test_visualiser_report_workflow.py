"""(C) Copyright 2026, by Ross Richardson
Exercise Visualiser candidate selection, repeat checks and the evidence passed to maintainers.
@author ross richardson
"""
import hashlib
import json
import hmac
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from .visualiser import report_delivery, report_workflow as workflow


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

    def test_publisher_signs_only_the_report_and_records_failed_runs_without_a_build(self):
        key = '12' * 32
        built = dict(outcome='scientific review',manifest='a'*64,frontend=FRONTEND,
                     reference='d'*40,rows=576,changed=13)
        env = dict(QUALIFICATION_RESULT='success',CHECKED_REPORT=json.dumps(built),GITHUB_SHA='b'*40,
                   VISUALISER_REVISION=REV,GITHUB_REPOSITORY='simpaths/SimPaths',GITHUB_RUN_ID='1234',
                   GITHUB_RUN_ATTEMPT='1',REPORT_KEY=key,MULTIRUN_ORIGIN='https://review.example.org')
        with patch.dict(os.environ,env), patch('http.client.HTTPSConnection') as connect:
            conn = connect.return_value
            conn.getresponse.return_value.status = 200
            conn.getresponse.return_value.read.return_value = json.dumps(dict(candidate='e'*64))
            for result in ('success','failure'):
                os.environ['QUALIFICATION_RESULT'] = result
                report_delivery.main()
                method,path,body,headers = conn.request.call_args.args
                self.assertEqual((method,path,headers['Origin']),('POST','/admin/api/visualiser_report',env['MULTIRUN_ORIGIN']))
                data = json.loads(body)
                report = data['report']
                self.assertEqual(report['manifest'],built['manifest'] if result=='success' else None)
                self.assertEqual(report['outcome'],'scientific review' if result=='success' else 'failed')
                self.assertEqual((report['run'],report['visualiser']),(1234,REV))
                signed = json.dumps(report,sort_keys=True,separators=(',',':')).encode()
                self.assertEqual(data['signature'],hmac.digest(bytes.fromhex(key),signed,'sha256').hex())
            conn.getresponse.return_value.status = 302
            with self.assertRaisesRegex(RuntimeError,'HTTP 302'):
                report_delivery.main()
            with self.assertRaises(ValueError):
                report_delivery.send_report('http://review.example.org',bytes.fromhex(key),built)

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
