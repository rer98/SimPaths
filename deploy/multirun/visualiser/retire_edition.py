"""(C) Copyright 2026, by Ross Richardson
Publish an approved archive retirement and reconcile it with the verified installed build.
@author ross richardson
"""
import base64
from http.client import HTTPException, HTTPSConnection
import json
import re
from uuid import UUID

from ..visualiser_editions import CALCULATOR_FILES, REGISTRY, historical_editions, manifest_usage
from .bundle_installation import manifest_digest
from .github_artifact import ArtifactError

ARCHIVES = 'deploy/multirun/visualiser/historical'
BRANCHES = {'rer98/SimPaths': 'add-web-files', 'simpaths/SimPaths': 'main'}
ARCHIVE_FILES = {'build.json','runner.cjs','calculation.cjs','dependencies.LICENSE.txt',
                 'calculation.cjs.LICENSE.txt','visualiser.LICENSE.txt','COPYRIGHT.md'}


def commit_changes(token, commit):
    connection = HTTPSConnection('api.github.com', timeout=30)
    try:
        mutation = ('mutation($input:CreateCommitOnBranchInput!){'
                    'createCommitOnBranch(input:$input){commit{oid}}}')
        connection.request('POST', '/graphql', json.dumps(dict(query=mutation, variables={'input':commit})),
                           {'Authorization':'Bearer '+token, 'Content-Type':'application/json',
                            'User-Agent':'SimPaths-Visualiser-Retirement'})
        response = connection.getresponse()
        result = response.read(65537)
        if response.status != 200 or len(result)>65536:
            raise ArtifactError('GitHub declined the retirement commit. Check the retirement credential and branch rules.')
        data = json.loads(result)
        if data.get('errors'):
            raise ArtifactError('GitHub declined the retirement commit. Check branch permissions and refresh before retrying.')
        return data['data']['createCommitOnBranch']['commit']['oid']
    finally:
        connection.close()


def publish(github, token, request):
    repository, branch = github.repository, github.branch
    if BRANCHES.get(repository) != branch:
        raise ArtifactError('The configured branch is not enabled for Visualiser retirement.')
    edition, current = request['edition'], request['current_revision']
    if not all(re.fullmatch('[0-9a-f]{40}', value) for value in (edition,current)) or edition == current:
        raise ValueError('Expected a distinct historical revision')
    request_id = str(UUID(str(request['id'])))
    head = github.api(f'{repository}/git/ref/heads/{branch}')['object']['sha']
    if not re.fullmatch('[0-9a-f]{40}', head):
        raise ArtifactError('Invalid project revision')
    path = ARCHIVES+'/approved.json'
    file = github.api(f'{repository}/contents/{path}?ref={head}')
    registry = json.loads(base64.b64decode(file['content']))
    matches = [row for row in registry if row['revision'].startswith(edition[:8])]
    local = next((row for row in json.loads(REGISTRY.read_bytes()) if row['revision']==edition), None)
    if len(matches)!=1 or not local or any(matches[0].get(k)!=local.get(k) for k in
            ('revision','reader_commit','manifest_sha256','node','files')):
        raise ArtifactError('The project archive differs from the approved edition on this server.')
    target = matches[0]
    headline = f'Retire Visualiser {edition} from {current}'
    body = 'MultiRun retirement '+request_id
    if target.get('retirement_request') == request_id:
        # Recover a committed mutation whose HTTPS reply or local database write was lost.
        history = github.api(f'{repository}/commits?sha={head}&path={path}&per_page=100')
        for item in history:
            if item['commit']['message'].strip() == headline+'\n\n'+body:
                return item['sha']
        raise ArtifactError('The recorded retirement commit could not be located in project history.')
    if not token:
        raise ArtifactError('Configure the host github-retirement-token credential, then retry retirement.')
    deletions = []
    if not target.get('retired'):
        folder = ARCHIVES+'/'+edition[:8]
        files = github.api(f'{repository}/contents/{folder}?ref={head}')
        names = {item['name'] for item in files}
        if not {'build.json','runner.cjs','calculation.cjs'} <= names <= ARCHIVE_FILES or any(
                item['type']!='file' or item['path']!=folder+'/'+item['name'] for item in files):
            raise ArtifactError('The archive contains different files. Review the project archive before retiring it.')
        deletions = [{'path':folder+'/'+name} for name in sorted(names)]
    target.update(retired=True, retirement_request=request_id)
    content = base64.b64encode((json.dumps(registry, indent=2)+'\n').encode()).decode()
    return commit_changes(token, dict(branch=dict(repositoryNameWithOwner=repository, branchName=branch),
        expectedHeadOid=head, message=dict(headline=headline,body=body),
        fileChanges=dict(additions=[dict(path=path,contents=content)],deletions=deletions)))


def check_candidate(queue, project, installed, candidate):
    """Keep an administrator's retirement request bound to its edition and current calculator."""
    with queue._connection() as c:
        request = c.execute("""SELECT edition,current_revision FROM visualiser_retirements
            WHERE pool_id=%s AND project=%s ORDER BY requested_at DESC LIMIT 1""",
            (queue.pool_id,project)).fetchone()
    # The next scheduled Visualiser release can use the very same SimPaths commit.
    if not request or candidate['revision'] != request['current_revision']:
        return
    previous = [row for row,_ in historical_editions(installed)]
    remaining = [row for row in previous if row['revision'] != request['edition']]
    if (installed['revision'] != request['current_revision'] or candidate['revision'] != installed['revision'] or
            len(remaining) == len(previous) or remaining != [row for row,_ in historical_editions(candidate)] or
            installed['mode'] != candidate['mode'] or installed['toolchain']['node'] != candidate['toolchain']['node'] or
            any(installed['files'][name] != candidate['files'][name] for name in CALCULATOR_FILES)):
        raise ArtifactError('The qualified build differs from the requested retirement. Review the installed editions.')


def update(queue, request, state, note, project=None):
    with queue._connection() as c:
        c.execute("""UPDATE visualiser_retirements SET state=%s,note=%s,project=coalesce(%s,project)
            WHERE pool_id=%s AND id=%s AND state=%s""",
            (state,note,project,queue.pool_id,request['id'],request['state']))


def advance(queue, live, github, token, *, allow_publish=True):
    from jasmine_web.batch.visualiser_deployments import installed_evidence
    with queue._connection() as c:
        request = c.execute("""SELECT *,requested_at < statement_timestamp()-interval '1 day' AS overdue
            FROM visualiser_retirements WHERE pool_id=%s
            AND state IN ('requested','published') ORDER BY (state='published') DESC,requested_at,id LIMIT 1""",
            (queue.pool_id,)).fetchone()
    if not request:
        return
    manifest = json.loads((live/'build.json').read_bytes())
    if not installed_evidence(queue, github.repository, manifest_digest(live)):
        if allow_publish and request['state']=='requested':
            update(queue,request,'attention','The current build has no verified installation receipt. Verify it before retrying.')
        return
    edition = request['edition']
    if edition == manifest['revision']:
        update(queue,request,'attention','The requested edition is now current.')
        return
    if edition not in {row['revision'] for row,_ in historical_editions(manifest)}:
        if request['state']=='published' and request['project']:
            update(queue,request,'retired','Retirement verified in the installed build.')
        else:
            update(queue,request,'attention','The installed build changed before this retirement was published.')
        return
    if manifest['revision'] != request['current_revision']:
        update(queue,request,'attention','The current Visualiser revision changed. Review the new usage report.')
        return
    if request['state']=='published':
        with queue._connection() as c:
            result = c.execute("""SELECT r.state,i.state AS installation,i.note FROM visualiser_update_reviews r
                LEFT JOIN LATERAL (SELECT state,note FROM visualiser_installations
                    WHERE pool_id=r.pool_id AND candidate=r.candidate ORDER BY updated_at DESC LIMIT 1) i ON true
                WHERE r.pool_id=%s AND r.evidence->>'repository'=%s AND r.evidence->>'project'=%s
                ORDER BY r.received_at DESC LIMIT 1""",(queue.pool_id,github.repository,request['project'])).fetchone()
        if result and (result['state'] == 'failed' or result['installation'] in
                       ('rolled_back','failed','cancelled','installed')):
            update(queue,request,'attention',result['note'] if result['installation'] in ('rolled_back','failed','cancelled')
                   else 'Review the qualification and installation result before retrying retirement.')
        elif result and result['state'] in ('awaiting','deferred'):
            note = ('The qualification is awaiting administrator review.' if result['state']=='awaiting' else
                    'The update was deferred. Reopen its review to continue.')
            if request['note'] != note:
                update(queue,request,'published',note)
        elif not result and request['overdue']:
            update(queue,request,'attention','No qualification report arrived within a day. Check the workflow and retry.')
        return
    if not allow_publish:
        return
    with queue._connection() as c:
        usage = manifest_usage(c,queue.pool_id,manifest,manifest['toolchain']['node'])
    if usage['unmatched'] or any(row['sources'] for row in usage['editions'] if row['revision']==edition):
        update(queue,request,'attention','Protected sources changed. Refresh the usage report.')
        return
    try:
        project = publish(github,token,request)
        if not re.fullmatch('[0-9a-f]{40}',project):
            raise ArtifactError('GitHub returned an invalid project commit.')
    except ArtifactError as error:
        update(queue,request,'attention',str(error))
        return
    except (OSError, HTTPException):
        # GitHub may already have received the commit. The same request ID recovers it on the next tick.
        if request['overdue']:
            update(queue,request,'attention','GitHub could not be reached. Inspect the branch before retrying.')
        return
    update(queue,request,'published','The project change was submitted for qualification and installation.',project)
