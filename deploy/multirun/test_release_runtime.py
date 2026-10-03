"""(C) Copyright 2026, by Ross Richardson

Disposable PostgreSQL/HTTP release-transition proof using fictional prepared files.
Checks accepted reviews, waiting jobs, retries, ownership and version compatibility.
@author ross richardson
"""
from copy import deepcopy
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from deploy._workflow import frontend_path
sys.path.insert(0,str(frontend_path()))
sys.path.insert(0,str(frontend_path()/'tests'/'batch'))
from starlette.testclient import TestClient
from jasmine_web.batch.access import Access
from jasmine_web.batch.browser import COOKIE, create_app
from jasmine_web.batch.datasets import Datasets
from jasmine_web.batch.submission_service import Submissions
from jasmine_web.batch.store import Queue
import test_postgres

from .artifacts import fingerprint
from .browser_model import BrowserModel
from .queue_adapter import read_prepared
from .releases import ReleaseRegistry
from .resource_policy import DEFAULT_POLICY, LEGACY_POLICY
from .submission_adapter import PreparationAdapter

ORIGIN='https://releases.example.org'
IMAGE_A='sha256:'+'a'*64
IMAGE_B='sha256:'+'b'*64


@unittest.skipUnless(os.environ.get('JASMINE_BATCH_TEST_DSN'),'Use release_proof.py with disposable PostgreSQL')
class ReleaseRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_postgres.PostgresQueueTests()
        self.addCleanup(self.fixture.doCleanups); self.fixture.setUp()
        self.q=self.fixture.q
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.jar=self.root/'model.jar'; self.jar.write_bytes(b'fictional model A')
        self.defaults=self.root/'defaults'; self.defaults.mkdir()
        (self.defaults/'scenario_CPI.xlsx').write_bytes(b'fictional parameters A')
        self.registry=ReleaseRegistry(self.root/'state')
        self.first=self.registry.register(image=IMAGE_A,name='Version A',jar=self.jar,defaults=self.defaults)
        self.mail={}
        async def deliver(email,code): self.mail[email]=code
        self.access=Access(self.q,'fictional-persistent-release-secret-'*2,deliver)
        self.datasets=Datasets(self.q,self.root/'uploads',reserve_bytes=1)
        self.owner=self.access.approve_email('alice@example.org')
        self.restart()
        self.login(self.client,'alice@example.org')
        self.auth=self.client.cookies.get(COOKIE)
        self.uploads=[]
        for name in ('population_initial_UK_2019.csv','policy.txt'):
            self.uploads.append(self.datasets.receive(self.owner,name,io.BytesIO(b'fictional input'),expected_bytes=15))

    def restart(self):
        old=getattr(self,'client',None)
        self.service=Submissions(self.access,self.datasets,BrowserModel(self.registry.load()))
        self.client=TestClient(create_app(self.service,origin=ORIGIN),base_url=ORIGIN,
                               headers={'Origin':ORIGIN})
        self.addCleanup(self.client.close)
        if old:
            self.client.cookies.update(old.cookies)
            self.client.headers['X-CSRF-Token']=old.headers['X-CSRF-Token']
        self.adapter=PreparationAdapter(self.datasets,self.service.model.releases,self.root/'artifacts')

    def login(self,client,email):
        challenge=client.post('/api/code',json=dict(email=email)).json()['challenge']
        response=client.post('/api/verify',json=dict(challenge=challenge,code=self.mail[email]))
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['authorised'])
        client.headers['X-CSRF-Token']=response.json()['csrf']

    def upgrade(self, *, same_model=False):
        if not same_model: self.jar.write_bytes(b'fictional model B')
        (self.defaults/'scenario_CPI.xlsx').write_bytes(b'fictional parameters B')
        policy=deepcopy(DEFAULT_POLICY); policy['simulation']['storage']['setup_mib']=11264
        self.second=self.registry.register(image=IMAGE_B,name='Version B',jar=self.jar,
            defaults=self.defaults,policy=policy,make_default=True)
        self.restart()
        self.assertEqual(self.client.cookies.get(COOKIE),self.auth)

    def preparation_review(self,release):
        response=self.client.post('/api/review-preparation',json=dict(release=release,upload_ids=self.uploads,
            selection=dict(year=2019,schedule=[['policy.txt','2019','2019','Baseline']]),dataset_name='Inputs '+release[-8:]))
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['review']

    def submit(self,review):
        response=self.client.post('/api/submit',json=dict(key=uuid4().hex,review=review))
        self.assertEqual(response.status_code,201,response.text)
        return response.json()['id']

    def pending_dataset(self,experiment):
        response=self.client.get('/api/experiments/'+experiment)
        self.assertEqual(response.status_code,200,response.text)
        value=response.json()
        dataset=value['dataset'] or value['pending_dataset']
        self.assertIsInstance(dataset,str,response.text)
        return dataset

    def form(self,release):
        return dict(name='Release transition',model_release=release,
            common=dict(population=20000,start_year=2019,end_year=2020),repetitions=3,
            first_seed='606',run_sets=[dict(id='policy',name='Policy',model_args={})],baseline=None,auto_retry=True)

    def experiment_review(self,dataset,release):
        return self.client.post('/api/review-experiment',json=dict(dataset=dataset,form=self.form(release)))

    def publish(self,lease):
        """Exercise the real immutable file/publication adapter with synthetic output."""
        self.assertIsNone(self.q.started(lease))
        base=self.root/lease.execution_key; base.mkdir(mode=0o700)
        request=base/'request'; request.mkdir(); work=base/'work'; work.mkdir()
        with patch('deploy.multirun.submission_adapter.subprocess.run'):
            command=self.adapter.container_command(lease,request)
        sources=Path(command.inputs)
        shutil.copytree(sources/'defaults',work/'input')
        params=self.adapter.parameters(lease)
        for name in params['uploads']:
            sub='InitialPopulations' if name.endswith('.csv') else 'EUROMODoutput'
            path=work/'input'/sub/name; path.parent.mkdir(exist_ok=True)
            shutil.copyfile(sources/'uploads'/name,path)
        for name in ('input.mv.db','tax_donor_population_UK.csv','DatabaseCountryYear.xlsx','EUROMODpolicySchedule.xlsx'):
            (work/'input'/name).write_bytes(b'fictional generated input')
        (base/'execution.log').write_text('MULTIRUN_INPUTS_PREPARED_AND_VALIDATED\n')
        receipts=self.adapter.validate(lease,work)
        self.q.record_repetition(lease,0,'0',receipts[0]['fingerprint'])
        result=self.q.finish(lease,outcome='success',stop_evidence='f'*64,
                            on_success=self.adapter.publication(lease,work,receipts))
        self.assertEqual(result,'succeeded')
        return read_prepared(self.adapter.artifacts/lease.execution_key)

    def test_reviewed_old_preparation_submits_after_upgrade_and_uses_old_bundle(self):
        review=self.preparation_review(self.first)
        self.upgrade()
        experiment=self.submit(review)
        lease=self.q.claim('retained-preparer')
        self.assertEqual(lease.specification['model_digest'],IMAGE_A)
        params=self.adapter.parameters(lease)['model']['release']
        self.assertEqual(params['release'],self.first)
        self.assertEqual(params['model'],fingerprint(self.service.model.releases[self.first]['jar']))
        receipt=self.publish(lease)
        self.assertEqual(receipt['identity']['source_image'],IMAGE_A)
        self.assertEqual(receipt['identity']['release']['id'],self.first)
        self.assertEqual(receipt['identity']['release']['resource_policy'],DEFAULT_POLICY)
        dashboard=self.client.get('/api/dashboard').json()
        self.assertEqual(dashboard['form']['releases'][0]['id'],self.second)
        self.assertIn(self.first,[r['id'] for r in dashboard['form']['releases']])
        self.assertEqual(next(d for d in dashboard['datasets'] if d['id']==self.pending_dataset(experiment))['model_release'],self.first)

    def test_waiting_job_publication_and_retry_keep_old_model_and_resources(self):
        preparation=self.submit(self.preparation_review(self.first)); dataset=self.pending_dataset(preparation)
        review=self.experiment_review(dataset,self.first)
        self.assertEqual(review.status_code,200,review.text)
        experiment=self.submit(review.json()['review'])
        before=self.fixture.sql('SELECT resources FROM jobs WHERE experiment_id=%s',(experiment,))[0]['resources']
        self.upgrade()
        prep=self.q.claim('preparer'); self.publish(prep)
        self.service.lifecycle.reconcile()
        lease=self.q.claim('model-A')
        self.assertEqual(lease.specification['model_digest'],IMAGE_A)
        self.assertEqual(lease.resources,before)
        self.assertEqual(lease.specification['run_sets'][0]['parameters']['model_release'],self.first)
        self.assertIsNone(self.q.started(lease))
        self.assertEqual(self.q.finish(lease,outcome='transient',stop_evidence='a'*64),'retry_wait')
        # Reopen both the queue and service: retry must not consult the default.
        self.q=Queue(self.q.dsn,self.q.pool_id,schema=self.q.schema)
        self.access.queue=self.q; self.datasets.queue=self.q; self.fixture.q=self.q
        self.restart(); self.fixture.ready_retries()
        retry=self.q.claim('restarted-model-A')
        self.assertNotEqual(retry.attempt_id,lease.attempt_id)
        self.assertEqual(retry.resources,lease.resources)
        self.assertEqual(retry.specification,lease.specification)
        self.assertEqual(retry.specification['seeds'],['606','607','608'])

    def test_new_preparations_use_new_version_and_incompatible_comparisons_are_denied(self):
        first_prep=self.submit(self.preparation_review(self.first)); first_data=self.pending_dataset(first_prep)
        self.publish(self.q.claim('prepare-A'))
        self.upgrade()
        second_prep=self.submit(self.preparation_review(self.second)); second_data=self.pending_dataset(second_prep)
        receipt=self.publish(self.q.claim('prepare-B'))
        self.assertEqual(receipt['identity']['source_image'],IMAGE_B)
        self.assertEqual(receipt['identity']['release']['id'],self.second)
        for dataset,release in ((first_data,self.first),(second_data,self.second)):
            response=self.experiment_review(dataset,release)
            self.assertEqual(response.status_code,200,response.text)
        wrong=self.experiment_review(first_data,self.second)
        self.assertEqual(wrong.status_code,400,wrong.text)
        form=self.form(self.first)
        form['run_sets'].append(dict(id='alternative',name='Alternative',model_args={},dataset_revision=second_data))
        response=self.client.post('/api/review-experiment',json=dict(dataset=first_data,form=form))
        self.assertEqual(response.status_code,400,response.text)
        self.assertIn('same model version',response.json()['error'])
        self.access.approve_email('bob@example.org')
        other=TestClient(create_app(self.service,origin=ORIGIN),base_url=ORIGIN,headers={'Origin':ORIGIN})
        self.addCleanup(other.close); self.login(other,'bob@example.org')
        denied=other.post('/api/review-experiment',json=dict(dataset=first_data,form=self.form(self.first)))
        self.assertEqual(denied.status_code,403,denied.text)
        self.assertNotIn(first_data,str(other.get('/api/dashboard').json()['datasets']))

    def test_prepared_receipts_keep_old_policy_when_same_model_gets_new_defaults_and_policy(self):
        prep=self.submit(self.preparation_review(self.first)); data=self.pending_dataset(prep)
        self.publish(self.q.claim('prepare-A'))
        response=self.experiment_review(data,self.first)
        self.assertEqual(response.status_code,200,response.text)
        reviewed=response.json()['review']
        self.upgrade(same_model=True)
        original=self.submit(reviewed)
        old_job=self.fixture.sql('SELECT * FROM jobs WHERE experiment_id=%s',(original,))[0]
        self.assertEqual(old_job['resources']['storage_mib'],5632)
        new_prep=self.submit(self.preparation_review(self.second)); new_data=self.pending_dataset(new_prep)
        # Finish the already accepted old run synthetically so preparation can
        # claim this owner's slot, without modifying its policy or history.
        self.fixture.complete(self.q.claim('old-model'))
        self.publish(self.q.claim('prepare-B'))
        response=self.experiment_review(new_data,self.second)
        self.assertEqual(response.status_code,200,response.text)
        newer=self.submit(response.json()['review'])
        new_job=self.fixture.sql('SELECT * FROM jobs WHERE experiment_id=%s',(newer,))[0]
        self.assertEqual(new_job['resources']['storage_mib'],12800)
        self.assertEqual(old_job['model_digest'],IMAGE_A)
        self.assertEqual(new_job['model_digest'],IMAGE_B)

    def test_storage_review_and_signed_resources_remain_frozen_after_default_change(self):
        prep=self.submit(self.preparation_review(self.first)); data=self.pending_dataset(prep)
        pending=self.experiment_review(data,self.first)
        self.assertEqual(pending.status_code,200,pending.text)
        self.assertEqual(pending.json()['storage'][0]['storage_mib'],5632)
        self.assertTrue(pending.json()['storage'][0]['provisional'])
        self.publish(self.q.claim('prepare'))
        reviewed=self.experiment_review(data,self.first)
        self.assertEqual(reviewed.status_code,200,reviewed.text)
        item=reviewed.json()['storage'][0]
        self.assertEqual((item['setup_mib'],item['per_repetition_mib'],item['repetitions']),(4096,512,3))
        self.assertFalse(item['provisional'])
        self.assertNotIn(str(self.root),reviewed.text)
        self.assertEqual(self.client.post('/api/review-experiment',json=dict(dataset=data,form=self.form(self.first)),
            headers={'X-CSRF-Token':'bad'}).status_code,403)
        self.upgrade(same_model=True)
        exp=self.submit(reviewed.json()['review'])
        job=self.fixture.sql('SELECT resources FROM jobs WHERE experiment_id=%s',(exp,))[0]
        self.assertEqual(job['resources']['storage_mib'],item['storage_mib'])

    def test_excessive_repetitions_fail_review_before_jobs_or_attempts_are_created(self):
        prep=self.submit(self.preparation_review(self.first)); data=self.pending_dataset(prep)
        self.service.model=BrowserModel(self.registry.load(),max_repetitions=1000)
        form=self.form(self.first); form['repetitions']=1000
        before=self.fixture.sql('SELECT count(*) AS n FROM jobs')[0]['n']
        response=self.client.post('/api/review-experiment',json=dict(dataset=data,form=form))
        self.assertEqual(response.status_code,400,response.text)
        self.assertIn('504.00 GiB',response.json()['error'])
        self.assertIn('Reduce the number of repetitions',response.json()['error'])
        self.assertEqual(self.fixture.sql('SELECT count(*) AS n FROM jobs')[0]['n'],before)
        self.assertEqual(self.q.occupancy()['attempts'],0)

    def test_pending_prepared_size_raise_is_checked_before_admission_without_spending_attempt(self):
        prep=self.submit(self.preparation_review(self.first)); data=self.pending_dataset(prep)
        exp=self.submit(self.experiment_review(data,self.first).json()['review'])
        self.publish(self.q.claim('prepare'))
        with patch('deploy.multirun.queue_adapter.workspace_required_bytes',return_value=30*1024**3):
            self.service.lifecycle.reconcile()
        job=self.fixture.sql('SELECT * FROM jobs WHERE experiment_id=%s',(exp,))[0]
        self.assertEqual((job['state'],job['attempts'],job['spent_seconds']),('blocked',0,0))
        self.assertIn('execution profile',job['input_issue'])
        self.assertIsNone(self.q.claim('cannot-fit'))

    def test_pending_prepared_size_raise_fits_pool_and_is_frozen_for_retry(self):
        prep=self.submit(self.preparation_review(self.first)); data=self.pending_dataset(prep)
        exp=self.submit(self.experiment_review(data,self.first).json()['review'])
        self.publish(self.q.claim('prepare'))
        with patch('deploy.multirun.queue_adapter.workspace_required_bytes',return_value=6*1024**3+1):
            self.service.lifecycle.reconcile()
        lease=self.q.claim('larger-inputs')
        self.assertEqual(lease.resources['storage_mib'],7681)
        self.q.started(lease)
        self.assertEqual(self.q.finish(lease,outcome='transient',stop_evidence='a'*64),'retry_wait')
        self.upgrade(same_model=True); self.fixture.ready_retries()
        retry=self.q.claim('retry')
        self.assertEqual(retry.resources,lease.resources)
        self.assertEqual(retry.specification,lease.specification)

    def test_review_uses_configured_capacity_while_existing_configuration_is_running(self):
        prep=self.submit(self.preparation_review(self.first)); data=self.pending_dataset(prep)
        self.publish(self.q.claim('prepare'))
        exp=self.submit(self.experiment_review(data,self.first).json()['review'])
        self.q.started(self.q.claim('running'))
        response=self.experiment_review(data,self.first)
        self.assertEqual(response.status_code,200,response.text)
        another=self.submit(response.json()['review'])
        self.assertNotEqual(another,exp)
        self.assertEqual(self.q.occupancy()['attempts'],1)

    def test_registered_fixed_policy_is_not_rewritten_by_new_scaled_defaults(self):
        old=self.registry.register(image=IMAGE_A,name='Retained fixed policy',jar=self.jar,
            defaults=self.defaults,policy=LEGACY_POLICY)
        self.registry.select(old); self.restart()
        prep=self.submit(self.preparation_review(old)); data=self.pending_dataset(prep)
        self.publish(self.q.claim('prepare-fixed'))
        review=self.experiment_review(data,old)
        self.assertEqual(review.status_code,200,review.text)
        self.assertEqual(review.json()['storage'][0]['storage_mib'],10240)
        self.assertEqual(review.json()['storage'][0]['per_repetition_mib'],0)
        self.registry.select(self.first); self.restart()
        exp=self.submit(review.json()['review'])
        self.assertEqual(self.fixture.sql('SELECT resources FROM jobs WHERE experiment_id=%s',(exp,))[0]['resources']['storage_mib'],10240)
