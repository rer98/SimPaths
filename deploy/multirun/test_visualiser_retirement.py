"""(C) Copyright 2026, by Ross Richardson
Exercise retirement commits, retries and verification against the running Visualiser.
@author ross richardson
"""
import base64
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, MagicMock, patch
from uuid import uuid4

from .visualiser import retire_edition as retirement
from .visualiser.report_workflow import rehearsal_revision


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads(retirement.REGISTRY.read_bytes())
        self.edition = self.registry[0]['revision']
        self.request = dict(id=uuid4(),edition=self.edition,current_revision='a'*40,
                            state='requested',project=None,overdue=False)
        self.github = Mock(repository='rer98/SimPaths',branch='add-web-files')
        self.head = 'c'*40
        self.commit = 'd'*40
        self.files = ['build.json','runner.cjs','calculation.cjs','COPYRIGHT.md']
        self.history = []
        def metadata(path):
            if '/git/ref/' in path:
                return dict(object=dict(sha=self.head))
            if '/commits?' in path:
                return self.history
            self.assertTrue(path.endswith('?ref='+self.head))
            if '/approved.json?' in path:
                return dict(content=base64.b64encode(json.dumps(self.registry).encode()).decode())
            folder = retirement.ARCHIVES+'/'+self.edition[:8]
            self.assertIn('/contents/'+folder+'?',path)
            return [dict(name=name,type='file',path=folder+'/'+name) for name in self.files]
        self.github.api.side_effect = metadata

    def test_publish_once_with_exact_parent_and_scoped_archive_paths(self):
        with patch.object(retirement,'commit_changes',return_value=self.commit) as send:
            self.assertEqual(retirement.publish(self.github,'fixture',self.request),self.commit)
            commit = send.call_args.args[1]
            self.assertEqual(commit['expectedHeadOid'],self.head)
            changes = commit['fileChanges']
            self.assertEqual([item['path'] for item in changes['deletions']],
                             [retirement.ARCHIVES+'/'+self.edition[:8]+'/'+name for name in sorted(self.files)])
            addition, = changes['additions']
            updated = json.loads(base64.b64decode(addition['contents']))
            self.assertEqual(updated[1:],self.registry[1:])
            self.assertEqual(updated[0],dict(self.registry[0],retired=True,retirement_request=str(self.request['id'])))
            message = commit['message']['headline']+'\n\n'+commit['message']['body']
            tip = dict(id=self.commit,message=message)
            for repo,branch in retirement.BRANCHES.items():
                self.assertEqual(rehearsal_revision(repo,branch,self.commit,tip),self.request['current_revision'])
            self.assertIsNone(rehearsal_revision('rer98/SimPaths','main',self.commit,tip))
            self.registry=updated
            self.history=[dict(sha=self.commit,commit=dict(message=message))]
            self.assertEqual(retirement.publish(self.github,None,self.request),self.commit)
            self.assertEqual(send.call_count,1)

    def test_candidate_may_remove_only_the_requested_reader_from_the_same_calculation(self):
        queue=MagicMock()
        queue._connection.return_value.__enter__.return_value.execute.return_value.fetchone.return_value=self.request
        current = self.request['current_revision']
        def build(history):
            return dict(revision=current,mode='development-paired',toolchain={'node':'v22.23.1'},
                        files={'calculation.cjs':'a'*64,'runner.cjs':'b'*64},history=history)
        first,second=self.registry[:2]
        with patch.object(retirement,'historical_editions',side_effect=lambda build:[(row,'') for row in build['history']]):
            installed=build([first,second])
            candidate=build([second])
            retirement.check_candidate(queue,self.commit,installed,candidate)
            # Tomorrow's scheduled release uses the same deployment source and its own qualification.
            retirement.check_candidate(queue,self.commit,candidate,dict(candidate,revision='e'*40))
            for changed in (build([]),installed,
                            dict(candidate,files=dict(candidate['files'],**{'runner.cjs':'f'*64}))):
                with self.assertRaises(retirement.ArtifactError):
                    retirement.check_candidate(queue,self.commit,installed,changed)

    def test_unrecognised_archive_and_missing_credential_leave_git_untouched(self):
        with patch.object(retirement,'commit_changes') as send:
            with self.assertRaisesRegex(retirement.ArtifactError,'credential'):
                retirement.publish(self.github,None,self.request)
            self.files.append('new-archive-format')
            with self.assertRaisesRegex(retirement.ArtifactError,'different files'):
                retirement.publish(self.github,'fixture',self.request)
            send.assert_not_called()

    def test_worker_reports_retired_only_after_verified_installation(self):
        queue = MagicMock()
        db = queue._connection.return_value.__enter__.return_value
        live = MagicMock(spec=Path)
        manifest = dict(revision=self.request['current_revision'],toolchain=dict(node='v22.23.1'))
        (live/'build.json').read_bytes.return_value=json.dumps(manifest).encode()
        with patch('jasmine_web.batch.visualiser_deployments.installed_evidence') as installed, \
                patch.object(retirement,'manifest_digest',return_value='f'*64), \
                patch.object(retirement,'historical_editions') as history, \
                patch.object(retirement,'manifest_usage') as usage, \
                patch.object(retirement,'publish',return_value=self.commit) as publish, \
                patch.object(retirement,'update') as update:
            db.execute.return_value.fetchone.return_value=self.request
            installed.return_value=None
            retirement.advance(queue,live,self.github,None)
            self.assertEqual(update.call_args.args[2],'attention')
            history.return_value=[(dict(revision=self.edition),'archive-')]
            usage.return_value=dict(unmatched=0,editions=[dict(revision=self.edition,sources=2)])
            installed.return_value={'evidence':{}}
            retirement.advance(queue,live,self.github,None)
            self.assertEqual(update.call_args.args[2],'attention')
            publish.assert_not_called()
            usage.return_value['editions'][0]['sources']=0
            retirement.advance(queue,live,self.github,'fixture')
            self.assertEqual(update.call_args.args[2],'published')
            self.request.update(state='published',project=self.commit)
            db.execute.return_value.fetchone.side_effect=[self.request,dict(state='failed',installation=None,note=None)]
            retirement.advance(queue,live,self.github,None)
            self.assertEqual(update.call_args.args[2],'attention')
            db.execute.return_value.fetchone.side_effect=None
            history.return_value=[]
            retirement.advance(queue,live,self.github,None)
            self.assertEqual(update.call_args.args[2],'retired')
