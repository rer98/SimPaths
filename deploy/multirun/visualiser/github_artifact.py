"""(C) Copyright 2026, by Ross Richardson
Fetch the qualification artifact from its trusted GitHub workflow run and attempt.
@author ross richardson
"""
from datetime import datetime
from http.client import HTTPSConnection
import json
import re
from urllib.parse import urlsplit

from .check_candidate import UPSTREAM
from .report_workflow import qualification_workflow, rehearsal_revision


class PendingRun(Exception):
    """The report can arrive while its publishing job is still finishing."""


class ArtifactError(ValueError):
    """Safe operator-facing reason for refusing an installation."""


class GitHub:
    def __init__(self, repository, branch, token):
        if repository not in ('rer98/SimPaths','simpaths/SimPaths') or not branch:
            raise ValueError('Configure a trusted SimPaths repository and branch')
        self.repository = repository
        self.branch = branch
        self.headers = {'Accept':'application/vnd.github+json','User-Agent':'SimPaths-Visualiser-Updater'}
        if token:
            self.headers['Authorization'] = 'Bearer '+token

    def api(self, path):
        conn = HTTPSConnection('api.github.com',timeout=30)
        try:
            conn.request('GET','/repos/'+path,headers=self.headers)
            response = conn.getresponse()
            if response.status != 200:
                raise ArtifactError(f'GitHub metadata was unavailable (HTTP {response.status}).')
            data = response.read(16*1024**2+1)
            if len(data)>16*1024**2:
                raise ArtifactError('GitHub metadata exceeded the supported size.')
            return json.loads(data)
        finally:
            conn.close()

    def locate(self, evidence, installed_revision):
        if evidence['repository'] != self.repository:
            raise ArtifactError('The report belongs to a different project.')
        run = int(evidence['run'])
        attempt = int(evidence['attempt'])
        base = f'{self.repository}/actions/runs/{run}'
        attempted = base+f'/attempts/{attempt}'
        record = self.api(attempted)
        if (record['id'] != run or record['run_attempt'] != attempt or not qualification_workflow(record,self.branch) or
                record['head_branch'] != self.branch or record['head_sha'] != evidence['project'] or
                record['head_repository']['full_name'] != self.repository or
                record['repository']['full_name'] != self.repository):
            raise ArtifactError('The GitHub workflow, branch or project revision differs from the review.')
        if record['event'] == 'push':
            requested = rehearsal_revision(self.repository, self.branch, record['head_sha'], record.get('head_commit'))
            if not requested or requested != evidence['visualiser']:
                raise ArtifactError('The pushed commit does not request this Visualiser rehearsal.')
        elif record['event'] not in ('schedule','workflow_dispatch'):
            raise ArtifactError('Unexpected GitHub event for Visualiser qualification.')
        if record['status'] != 'completed':
            raise PendingRun()
        if record['conclusion'] != 'success':
            raise ArtifactError('The reported GitHub workflow attempt did not succeed.')
        jobs = self.api(attempted+'/jobs?per_page=100')['jobs']
        qualified = [job for job in jobs if job['name']=='qualify']
        if not qualified and attempt>1:
            # GitHub can rerun only the publishing job after a delivery failure.
            # Find the qualification whose output that later job reused.
            history = self.api(base+'/jobs?filter=all&per_page=100')
            if history['total_count']>100:
                raise ArtifactError('Run a fresh qualification to identify the producing attempt.')
            previous = [job for job in history['jobs'] if job['name']=='qualify' and job['run_attempt']<attempt]
            if previous:
                producing = max(job['run_attempt'] for job in previous)
                qualified = [job for job in previous if job['run_attempt']==producing]
        if len(qualified)!=1 or qualified[0]['conclusion']!='success':
            raise ArtifactError('The reported run has no successful qualification for this attempt.')
        start, end = (datetime.fromisoformat(qualified[0][k]) for k in ('started_at','completed_at'))
        prefix = 'visualiser-'+evidence['visualiser']+'-'
        artifacts = self.api(base+'/artifacts?per_page=100')
        matches = []
        for item in artifacts['artifacts']:
            if (not item['expired'] and re.fullmatch(re.escape(prefix)+'[0-9a-f]{64}',item['name'])
                    and start <= datetime.fromisoformat(item['created_at']) <= end.replace(microsecond=999999)):
                matches.append(item)
        if artifacts['total_count']>100 or len(matches)!=1:
            raise ArtifactError('The exact qualification artifact is missing, expired or ambiguous. Run qualification again.')
        item = matches[0]
        if not re.fullmatch(r'sha256:[a-f0-9]{64}',item['digest'] or ''):
            raise ArtifactError('GitHub did not supply a verified artifact checksum.')
        if installed_revision != evidence['visualiser']:
            change = self.api(f"{UPSTREAM}/compare/{installed_revision}...{evidence['visualiser']}?per_page=1")
            if change['status'] != 'ahead' or change['merge_base_commit']['sha'] != installed_revision:
                raise ArtifactError('The candidate does not follow the installed upstream revision.')
        return int(item['id']),item['digest'].split(':')[1]

    def check_integration(self, previous, evidence):
        # Multiple builds of a single upstream commit may use different SimPaths integration code.
        if previous['project'] == evidence['project']:
            raise ArtifactError('The same project and Visualiser revisions produced different bundles. Review build reproducibility.')
        change = self.api(f"{self.repository}/compare/{previous['project']}...{evidence['project']}?per_page=1")
        if change['status']!='ahead' or change['merge_base_commit']['sha']!=previous['project']:
            raise ArtifactError('A newer or different SimPaths integration is already installed.')

    def download(self, identifier, destination):
        conn = HTTPSConnection('api.github.com',timeout=30)
        try:
            conn.request('GET',f'/repos/{self.repository}/actions/artifacts/{identifier}/zip',headers=self.headers)
            response = conn.getresponse()
            if response.status != 302:
                raise ArtifactError(f'GitHub could not supply the artifact download (HTTP {response.status}).')
            target = urlsplit(response.getheader('Location') or '')
        finally:
            conn.close()
        if target.scheme != 'https' or not target.hostname or target.username or target.password or target.fragment:
            raise ArtifactError('GitHub supplied an invalid download location.')
        # The signed download URL goes to artifact storage without the GitHub credential.
        conn = HTTPSConnection(target.hostname,target.port,timeout=60)
        try:
            conn.request('GET',target.path+('?' + target.query if target.query else ''))
            response = conn.getresponse()
            if response.status != 200:
                raise ArtifactError('GitHub artifact storage could not supply the qualified build.')
            total = 0
            with destination.open('xb') as stream:
                while block := response.read(1024**2):
                    total += len(block)
                    if total > 256*1024**2:
                        raise ArtifactError('The GitHub artifact exceeds the installer limit.')
                    stream.write(block)
        finally:
            conn.close()
