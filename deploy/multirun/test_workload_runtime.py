"""(C) Copyright 2026, by Ross Richardson
Disposable PostgreSQL/HTTP admission for measured releases and public training.
Only fictional files, recorded email codes and queue metadata are used.
@author ross richardson
"""
from copy import deepcopy
import os
import sys
import unittest
from uuid import uuid4

from deploy._workflow import frontend_path
sys.path[:0]=[str(frontend_path()),str(frontend_path()/'tests/batch')]
from starlette.testclient import TestClient
from jasmine_web.batch.access import Access
from jasmine_web.batch.browser import create_app
from jasmine_web.batch.datasets import Datasets
from jasmine_web.batch.policy import NotFound
from jasmine_web.batch.preparation import Preparations
from jasmine_web.batch.submission_service import Submissions
import test_postgres

from .browser_model import BrowserModel
from .local_web import register_training
from .public_training import NAME, certify
from .releases import ReleaseRegistry, atomic_json
from . import test_public_training
from .workload_envelope import extended_resource_policy


@unittest.skipUnless(os.environ.get('JASMINE_BATCH_TEST_DSN'),'Use workload_envelope_proof.py with disposable PostgreSQL')
class WorkloadRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.f=test_postgres.PostgresQueueTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.q=self.f.q
        # A fictional VPS-sized capacity permits the maximum storage review.
        self.f.sql('UPDATE pools SET cpu_millis=11000,memory_mib=40448,storage_mib=131072 WHERE id=%s',(self.q.pool_id,))
        self.p=test_public_training.PublicTrainingTests();self.p.setUp();self.addCleanup(self.p.doCleanups)
        self.registry=ReleaseRegistry(self.p.f.root/'state')
        common=dict(image=self.p.release['image'],jar=self.p.f.jar,defaults=self.p.f.defaults)
        self.old=self.registry.register(name='Original preview',**common)
        policy=extended_resource_policy(self.registry.load()[self.old]['resource_policy'])
        self.new=self.registry.register(name='Measured preview',policy=policy,make_default=True,**common)
        self.p.release_id=self.new;self.p.release=self.registry.load()[self.new]
        self.receipt=certify(self.p.prepared());atomic_json(self.p.f.output/'receipt.json',self.receipt)
        self.mail={}
        async def deliver(email,code):self.mail[email]=code
        self.access=Access(self.q,'fictional-workload-proof-secret-'*3,deliver)
        self.owner=self.access.approve_email('alice@example.org')
        self.other=self.access.approve_email('bob@example.org')
        self.datasets=Datasets(self.q,self.p.f.root/'uploads',reserve_bytes=1)
        self.preparations=Preparations(self.datasets)
        self.key=register_training(self.datasets,self.preparations,self.p.f.output)
        self.restart()
        self.client=self.login('alice@example.org')
        self.form=dict(name='Large training preview',model_release=self.new,
            common=dict(population=100000,start_year=2019,end_year=2070),repetitions=10,
            first_seed='606',baseline='baseline',auto_retry=True,
            run_sets=[dict(id='baseline',name='Baseline',model_args={})])

    def restart(self):
        self.service=Submissions(self.access,self.datasets,BrowserModel(self.registry.load(),max_repetitions=10))
        self.app=create_app(self.service,origin='https://workload.example.org')

    def login(self,email):
        client=TestClient(self.app,base_url='https://workload.example.org',headers={'Origin':'https://workload.example.org'})
        self.addCleanup(client.close)
        challenge=client.post('/api/code',json=dict(email=email)).json()['challenge']
        response=client.post('/api/verify',json=dict(challenge=challenge,code=self.mail[email]))
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['authorised'])
        client.headers['X-CSRF-Token']=response.json()['csrf']
        return client

    def review(self,form=None,client=None):
        return (client or self.client).post('/api/review-experiment',json=dict(dataset=self.key,form=form or self.form))

    def submit(self,form=None):
        reviewed=self.review(form);self.assertEqual(reviewed.status_code,200,reviewed.text)
        response=self.client.post('/api/submit',json=dict(key=uuid4().hex,review=reviewed.json()['review']))
        self.assertEqual(response.status_code,201,response.text)
        return response.json()['id'],reviewed.json()

    def test_maximum_workload_review_freezes_resources_and_survives_default_release_change(self):
        experiment,review=self.submit()
        self.assertEqual(review['seeds'],[str(seed) for seed in range(606,616)])
        self.assertEqual(review['resources'],dict(cpu_millis=2000,memory_mib=5120,storage_mib=51200))
        before=self.f.sql('SELECT specification,request_hash,resources FROM experiments WHERE id=%s',(experiment,))
        jobs=self.f.sql('SELECT resources,execution_run FROM jobs WHERE experiment_id=%s',(experiment,))
        self.registry.select(self.old);self.restart()
        self.assertEqual(before,self.f.sql('SELECT specification,request_hash,resources FROM experiments WHERE id=%s',(experiment,)))
        self.assertEqual(jobs,self.f.sql('SELECT resources,execution_run FROM jobs WHERE experiment_id=%s',(experiment,)))
        lease=self.q.claim('fictional-dispatcher')
        self.assertIsNotNone(lease)
        self.assertEqual(lease.resources,review['resources'])
        run=lease.specification['run_sets'][0]
        self.assertEqual(run['starting_allocation']['heap_mib'],4096)
        self.assertEqual(run['parameters']['model_release'],self.new)

    def test_public_example_is_available_without_grants_and_only_aggregates_are_downloadable(self):
        for client in (self.client,self.login('bob@example.org')):
            dashboard=client.get('/api/dashboard');self.assertEqual(dashboard.status_code,200,dashboard.text)
            row=next(item for item in dashboard.json()['datasets'] if item['id']==self.key)
            self.assertEqual(row['name'],NAME)
            self.assertEqual(row['locked'],['start_year'])
            reviewed=self.review(client=client);self.assertEqual(reviewed.status_code,200,reviewed.text)
        grants=self.f.sql('SELECT * FROM dataset_grants WHERE pool_id=%s AND dataset_id=%s',(self.q.pool_id,self.key))
        self.assertEqual(grants,[])
        experiment,_=self.submit()
        raw=self.datasets.register_artifact(experiment,'c'*64)
        aggregate=self.datasets.register_artifact(experiment,'d'*64,aggregate_approved=True)
        with self.assertRaises(NotFound):self.datasets.download(self.owner,raw)
        self.assertEqual(self.datasets.download(self.owner,aggregate),'d'*64)
        with self.assertRaises(NotFound):self.datasets.download(self.other,aggregate)

    def test_limits_and_release_pinning_are_checked_before_new_jobs_are_created(self):
        for change in (dict(population=100001),dict(end_year=2071)):
            form=deepcopy(self.form);form['common'].update(change)
            self.assertNotEqual(self.review(form).status_code,200)
        form=deepcopy(self.form);form['model_release']=self.old
        self.assertNotEqual(self.review(form).status_code,200)
        self.assertEqual(self.f.sql('SELECT id FROM jobs WHERE pool_id=%s',(self.q.pool_id,)),[])


if __name__=='__main__':unittest.main()
