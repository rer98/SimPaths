"""(C) Copyright 2026, by Ross Richardson
Verify the Visualiser worker's GitHub provenance, download and scientific baseline checks.
@author ross richardson
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from .visualiser import github_artifact as api, install_worker as worker
from .visualiser.report_workflow import WORKFLOW
from .visualiser_editions import REGISTRY


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.client = api.GitHub('rer98/SimPaths','add-web-files','private-test-token')
        self.evidence = dict(run=123,attempt=2,repository=self.client.repository,
                             project='d'*40,visualiser='b'*40)
        self.run = dict(id=123,run_attempt=2,path=WORKFLOW,head_sha='d'*40,head_branch=self.client.branch,
                        repository=dict(full_name=self.client.repository),head_repository=dict(full_name=self.client.repository),
                        event='workflow_dispatch',status='completed',conclusion='success')
        self.job = dict(name='qualify',conclusion='success',started_at='2026-11-27T11:00:00Z',completed_at='2026-11-27T11:01:00Z')
        self.item = dict(name='visualiser-'+'b'*40+'-'+'c'*64,id=21,expired=False,digest='sha256:'+'d'*64,
                         created_at='2026-11-27T11:00:50Z')
        self.change = dict(status='ahead',merge_base_commit=dict(sha='a'*40))
        def metadata(path):
            if '/compare/' in path:
                return self.change
            self.assertIn('/actions/runs/123/',path)
            if path.endswith('artifacts?per_page=100'):
                return dict(total_count=1,artifacts=[self.item])
            self.assertIn('/attempts/2',path)
            if path.endswith('jobs?per_page=100'):
                return dict(jobs=[self.job])
            return self.run
        self.client.api = metadata

    def locate(self):
        return self.client.locate(self.evidence,'a'*40)

    def test_exact_run_branch_attempt_job_and_archive(self):
        self.assertEqual(self.locate(),(21,'d'*64))
        with patch.dict(self.run,path=WORKFLOW+'@'+self.client.branch):
            self.assertEqual(self.locate(),(21,'d'*64))
        for record,key,value in [(self.run,'path','another.yml'),(self.run,'path',WORKFLOW+'@other'),(self.run,'run_attempt',1),
                (self.run,'head_sha','e'*40),(self.run,'event','pull_request'),(self.run,'head_branch','other'),
                (self.run,'head_repository',dict(full_name='another/repo')),(self.job,'conclusion','failure'),
                (self.item,'expired',True),(self.item,'created_at','2026-11-27T10:55:00Z'),
                (self.item,'name',self.item['name']+'-diagnostics'),(self.item,'digest',None),
                (self.change,'status','behind')]:
            with self.subTest(field=key),patch.dict(record,{key:value}),self.assertRaises(api.ArtifactError):
                self.locate()
        with patch.dict(self.run,status='in_progress'),self.assertRaises(api.PendingRun):
            self.locate()

    def test_retrying_only_report_delivery_uses_the_original_qualification(self):
        metadata = self.client.api
        def retried(path):
            if 'jobs?filter=all' in path:
                return dict(total_count=3,jobs=[dict(self.job,run_attempt=1)])
            if '/attempts/2/jobs' in path:
                return dict(jobs=[dict(name='publish',conclusion='success')])
            return metadata(path)
        self.client.api = retried
        self.assertEqual(self.locate(),(21,'d'*64))

    def test_push_artifact_must_match_fork_branch_and_visualiser_named_at_its_tip(self):
        head = dict(id=self.evidence['project'], message='Rehearse Visualiser '+self.evidence['visualiser'])
        self.run.update(event='push', head_commit=head)
        self.assertEqual(self.locate(),(21,'d'*64))
        for bad_head in (None, dict(head, id='e'*40), dict(head, message='An ordinary push'),
                         dict(head, message='Rehearse Visualiser '+'e'*40)):
            with self.subTest(head=bad_head), patch.dict(self.run,head_commit=bad_head), self.assertRaises(api.ArtifactError):
                self.locate()
        for repo, branch in (('simpaths/SimPaths','add-web-files'), ('rer98/SimPaths','main')):
            client = api.GitHub(repo, branch, None)
            client.api = self.client.api
            with self.subTest(repo=repo, branch=branch), patch.dict(self.run,head_branch=branch,
                    repository=dict(full_name=repo),head_repository=dict(full_name=repo)), self.assertRaises(api.ArtifactError):
                client.locate(dict(self.evidence, repository=repo), 'a'*40)

    def test_download_uses_credential_only_with_github(self):
        first,second = MagicMock(),MagicMock()
        first.getresponse.return_value.status = 302
        first.getresponse.return_value.getheader.return_value = 'https://storage.example.test/archive?sig=download'
        response = second.getresponse.return_value
        response.status = 200
        response.read.side_effect = [b'archive',b'']
        with patch.object(api,'HTTPSConnection',side_effect=[first,second]),tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/'download.zip'
            self.client.download(21,target)
            self.assertEqual(target.read_bytes(),b'archive')
        self.assertIn('Authorization',first.request.call_args.kwargs['headers'])
        second.request.assert_called_once_with('GET','/archive?sig=download')

    def test_scientific_comparison_must_apply_to_the_installed_method(self):
        reference = json.loads(REGISTRY.read_text())[-1]
        installed = dict(mode='development-paired',toolchain=dict(node=reference['node']),files=reference['files'])
        evidence = dict(reference=reference['revision'],outcome='scientific review')
        new = dict(installed,files={k:'c'*64 for k in reference['files']})
        worker.check_reference(installed,new,evidence)
        with self.assertRaises(api.ArtifactError):
            worker.check_reference(new,installed,evidence)
        with self.assertRaises(api.ArtifactError):
            worker.check_reference(installed,new,dict(evidence,outcome='checks passed'))
        worker.check_reference(new,new,evidence)
