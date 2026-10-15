"""(C) Copyright 2026, by Ross Richardson

Verify separated SingleRun rehearsal process ownership and credential isolation.
@author ross richardson
"""
import json
import hashlib
import copy
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from deploy.acceptance._single_rpc import SingleRPCProcesses,public_environment,drain_process,completed_private_evidence
from deploy.acceptance.run_security_refresh import (stages,exclusive_trials,reused_quickstart,
    reused_training,training_build_record,reused_interactive,interactive_resume_record,require_browser_space)
from deploy.acceptance import run_interactive_rpc_acceptance as interactive


class ProcessIsolation(unittest.TestCase):
    def test_resume_runs_only_remaining_image_and_browser_stages(self):
        commands,images=stages(Path('/web'),Path('/core'),Path('/evidence'),Path('/scratch'),Path('/maven'),
                              'security-test',resume=True,prune_build_cache=True)
        self.assertEqual([name for name,_ in commands],
                         ['interactive-training-image','interactive-user-data-image','interactive-browser'])
        self.assertFalse(any('build_release.py' in value for _,command in commands for value in command))
        self.assertIn('--prune-build-cache',commands[0][1])
        self.assertEqual(len(images),2)

    def test_completed_training_image_skips_only_its_build_and_still_runs_browser_checks(self):
        retained='simpaths-interactive:uk-training-security-original'
        commands,images=stages(Path('/web'),Path('/core'),Path('/evidence'),Path('/scratch'),Path('/maven'),
                              'security-test',resume=True,training_image=retained)
        self.assertEqual([name for name,_ in commands],['interactive-user-data-image','interactive-browser'])
        self.assertIn('--profile',commands[0][1])
        self.assertEqual(commands[0][1][commands[0][1].index('--profile')+1],'user-data')
        self.assertEqual(images[0],retained)
        self.assertIn(retained,commands[-1][1])

    def test_browser_only_resume_reuses_both_images_and_runs_only_user_data(self):
        commands,images=stages(Path('/web'),Path('/core'),Path('/evidence'),Path('/scratch'),Path('/maven'),
            'security-test',resume=True,training_image='candidate:training',user_data_image='candidate:user-data')
        self.assertEqual([name for name,_ in commands],['interactive-browser'])
        self.assertEqual(images,['candidate:training','candidate:user-data'])
        self.assertEqual(commands[0][1][commands[0][1].index('--profile')+1],'user-data')
        self.assertNotIn('--tag-suffix',commands[0][1])

    def test_browser_preparation_requires_free_space_after_images_are_built(self):
        with patch('deploy.acceptance.run_security_refresh.shutil.disk_usage') as usage:
            usage.return_value=SimpleNamespace(free=7.99*1024**3)
            with self.assertRaisesRegex(RuntimeError,'before launch'):require_browser_space('/evidence/new')
            usage.side_effect=[SimpleNamespace(free=9*1024**3),SimpleNamespace(free=7*1024**3)]
            with self.assertRaises(RuntimeError):require_browser_space('/evidence/new')
            usage.side_effect=None;usage.return_value=SimpleNamespace(free=9*1024**3)
            require_browser_space('/evidence/new')

    def test_overlapping_benchmark_or_public_lock_cannot_start_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with exclusive_trials(root):
                with self.assertRaises(BlockingIOError):
                    with exclusive_trials(root):self.fail('Overlapping heavy work was admitted')
            (root/'.simpaths-research-sequence.lock').chmod(0o644)
            with self.assertRaises(ValueError):
                with exclusive_trials(root):self.fail('Public lock was accepted')

    def test_private_shutdown_requires_a_receipt_and_successful_process_drain(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'private.json'
            with self.assertRaises(FileNotFoundError):completed_private_evidence(path)
            for invalid in ({},{'closed':True},{'closed':True,'errors':['Failure']},{'closed':1,'errors':[]}):
                path.write_text(json.dumps(invalid))
                with self.assertRaises(RuntimeError):completed_private_evidence(path)
            path.write_text(json.dumps({'closed':True,'errors':[]}))
            self.assertTrue(completed_private_evidence(path)['closed'])
        process=Mock(returncode=1,poll=Mock(return_value=1))
        with self.assertRaises(RuntimeError):drain_process(process)
        process=Mock(returncode=0,poll=Mock(return_value=None))
        process.wait.side_effect=[subprocess.TimeoutExpired('fixture',90),0]
        with self.assertRaises(RuntimeError):drain_process(process)
        process.kill.assert_called_once()

    def test_refresh_uses_candidate_tags_private_browser_checks_and_no_promotion(self):
        commands,images=stages(Path('/web'),Path('/core'),Path('/evidence'),Path('/scratch'),Path('/maven'),'security-test')
        flattened=[part for _,command in commands for part in command]
        self.assertIn('--candidate-worktree',flattened)
        self.assertIn('--private-rpc',flattened)
        self.assertNotIn('--promote',flattened)
        self.assertNotIn('--prune-build-cache',flattened)
        self.assertEqual(images,['simpaths-interactive:uk-training-security-test','simpaths-interactive:uk-user-data-security-test'])
        self.assertEqual(commands[-1][0],'interactive-browser')
    def test_public_environment_excludes_database_signing_and_operator_credentials(self):
        environment=dict(PATH='/bin',LANG='en_GB',VM_POSTGRES_DSN='secret',JASMINE_BATCH_TEST_DSN='secret',
            SESSION_SECRET='secret',PGPASSWORD='secret',ADMIN_PASSWORD='secret',SMTP_PASSWORD='secret',
            DOCKER_HOST='secret',OPENAI_API_KEY='secret',GITHUB_TOKEN='secret',PYTHONPATH='/private')
        self.assertEqual(public_environment(environment),dict(PATH='/bin',LANG='en_GB'))

    def test_public_restart_reuses_private_process_and_only_nonsecret_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            args=SimpleNamespace(frontend=root,nginx=None)
            processes=SingleRPCProcesses(root,root,args)
            private=Mock(pid=123,poll=Mock(return_value=None))
            processes.executor=private
            processes.socket.touch()
            settings=dict(origin='https://localhost:15443',app_port=15001,secret='PRIVATE_CANARY',dsn='PRIVATE_DSN')
            with patch('deploy.acceptance._single_rpc.subprocess.Popen') as spawn:
                processes.start_app(settings,script=root/'fixture.py',workdir=root)
                self.assertEqual(spawn.call_count,1)
                public=json.loads((root/'public-fixture.json').read_text())
                self.assertEqual(set(public),{'origin','port','socket'})
                self.assertNotIn('PRIVATE',json.dumps(public))
                self.assertIs(processes.executor,private)
                self.assertNotIn('JASMINE_BATCH_TEST_DSN',spawn.call_args.kwargs['env'])
            for log in processes.logs:log.close()
            import shutil
            shutil.rmtree(processes.rpc_directory)

    def test_unknown_or_unclean_private_shutdown_cannot_be_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            processes=SingleRPCProcesses(root,root,SimpleNamespace(frontend=root,nginx=None))
            process=Mock(returncode=0)
            processes.executor=process
            (root/'private-rpc.json').write_text(json.dumps(dict(closed=False,errors=[])))
            with self.assertRaises(RuntimeError):processes.stop_executor()
            import shutil
            shutil.rmtree(processes.rpc_directory)


class InteractiveAcceptanceCleanup(unittest.TestCase):
    def run_fixture(self, missing_receipt,profile='both'):
        """Passing browser results must not hide missing executor-shutdown proof."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);frontend=root/'source';output=root/'evidence'
            catalogue=frontend/'deploy/simpaths/models.json'
            catalogue.parent.mkdir(parents=True)
            catalogue.write_text(json.dumps({'models':[
                {'id':name,'name':name,'deployment':{'image':'old'}}
                for name in ('simpaths-uk-training','simpaths-uk-user-data')]}))
            rpc=root/'rpc';rpc.mkdir(mode=0o700)
            client=Mock();client.images.get.return_value.id='sha256:candidate'
            client.containers.list.return_value=[];client.networks.list.return_value=[]
            database=Mock()
            processes=[Mock(returncode=0,poll=Mock(return_value=None)) for _ in range(2)]
            spawned=[]
            def copy(source,destination):destination.mkdir()
            def postgres(client,token,port,work):
                database.labels={'simpaths.acceptance':token}
                dsn=work/'.dsn';dsn.write_text('PRIVATE_DATABASE_CANARY')
                return database,dsn
            def spawn(command,**settings):
                spawned.append(settings)
                return processes[len(spawned)-1]
            def browser(command,log):
                destination=Path(command[command.index('--output')+1])
                destination.mkdir();(destination/'report.json').write_text('{"status":"passed"}')
                if not missing_receipt:
                    (output/'private-rpc.json').write_text('{"closed":true,"errors":[]}')
            with patch('docker.from_env',return_value=client),patch('httpx.get',return_value=Mock(status_code=200)), \
                 patch('deploy.acceptance.postgres_fixture.start_postgres',side_effect=postgres), \
                 patch.object(interactive,'free_port',return_value=15001), \
                 patch.object(interactive,'copy_frontend',side_effect=copy), \
                 patch.object(interactive.tempfile,'mkdtemp',return_value=str(rpc)), \
                 patch.object(interactive.subprocess,'Popen',side_effect=spawn), \
                 patch.object(interactive,'run_browser_stage',side_effect=browser):
                code=interactive.main(['--frontend',str(frontend),'--training-image','candidate-training',
                                      '--user-data-image','candidate-user-data','--output',str(output),
                                      '--profile',profile])
            report=json.loads((output/'report.json').read_text())
            self.assertEqual(code,1 if missing_receipt else 0)
            self.assertEqual(report['cleanup'],not missing_receipt)
            self.assertEqual(report['passed'],not missing_receipt)
            self.assertEqual(report['profile'],profile)
            self.assertEqual(len(report['checks']),5 if profile=='both' else 1)
            if profile=='user-data':self.assertEqual(report['checks'][0]['name'],'user-data')
            self.assertNotIn('VM_POSTGRES_DSN_FILE',spawned[1]['env'])
            self.assertNotIn('SESSION_SECRET',spawned[1]['env'])
            self.assertTrue(spawned[0]['env']['SESSION_SECRET'])
            for process in processes:process.terminate.assert_called_once()
            database.remove.assert_called_once_with(force=True)

    def test_full_browser_acceptance_still_fails_if_private_cleanup_evidence_is_missing(self):
        self.run_fixture(True)

    def test_full_browser_acceptance_confirms_cleanup_and_keeps_credentials_private(self):
        self.run_fixture(False)

    def test_user_data_retry_keeps_private_cleanup_checks_without_repeating_training(self):
        self.run_fixture(False,profile='user-data')


class InteractiveBrowserOwnership(unittest.TestCase):
    def test_success_waits_for_driver_and_nonzero_exit_is_never_accepted(self):
        command=['python','owned-browser.py']
        for code in (0,1):
            with self.subTest(code=code):
                process=Mock(pid=1234)
                process.wait.return_value=code
                log=Mock()
                with patch.object(interactive.subprocess,'Popen',return_value=process) as spawn:
                    if code:
                        with self.assertRaises(subprocess.CalledProcessError) as error:
                            interactive.run_browser_stage(command,log)
                        self.assertEqual(error.exception.returncode,1)
                    else:
                        interactive.run_browser_stage(command,log)
                spawn.assert_called_once_with(command,stdout=log,stderr=subprocess.STDOUT,
                                              start_new_session=True)
                process.wait.assert_called_once_with(timeout=12*60*60)
                process.send_signal.assert_not_called()

    def test_interrupt_waits_for_browser_cleanup_before_returning_to_service_owner(self):
        process=Mock(pid=1234,poll=Mock(return_value=None))
        process.wait.side_effect=[KeyboardInterrupt(),0]
        with patch.object(interactive.subprocess,'Popen',return_value=process), \
             patch.object(interactive.os,'killpg') as kill:
            with self.assertRaises(KeyboardInterrupt):
                interactive.run_browser_stage(['browser'],Mock())
        process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual([call.kwargs['timeout'] for call in process.wait.call_args_list],
                         [12*60*60,150])
        kill.assert_not_called()

    def test_timeout_and_failed_graceful_cleanup_kill_only_owned_browser_group_and_reap(self):
        process=Mock(pid=1234,poll=Mock(return_value=None))
        process.wait.side_effect=[subprocess.TimeoutExpired('browser',12*60*60),
                                  subprocess.TimeoutExpired('browser',150),-signal.SIGKILL]
        with patch.object(interactive.subprocess,'Popen',return_value=process), \
             patch.object(interactive.os,'killpg') as kill:
            with self.assertRaisesRegex(RuntimeError,'graceful cleanup'):
                interactive.run_browser_stage(['browser'],Mock())
        process.send_signal.assert_called_once_with(signal.SIGINT)
        kill.assert_called_once_with(1234,signal.SIGKILL)
        self.assertEqual([call.kwargs['timeout'] for call in process.wait.call_args_list],
                         [12*60*60,150,10])

    def test_expired_trial_cannot_pass_even_if_interrupt_cleanup_succeeds(self):
        process=Mock(pid=1234,poll=Mock(return_value=None))
        process.wait.side_effect=[subprocess.TimeoutExpired('browser',12*60*60),0]
        with patch.object(interactive.subprocess,'Popen',return_value=process):
            with self.assertRaises(subprocess.TimeoutExpired):
                interactive.run_browser_stage(['browser'],Mock())
        process.send_signal.assert_called_once_with(signal.SIGINT)


class RetainedQuickStartEvidence(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.runtime='runtime-checksum'
        self.sources={name:dict(revision='revision',worktree='',files={'runtime.py':'source-hash'})
                      for name in ('core','simpaths','frontend')}
        self.sources['simpaths']['files']['deploy/acceptance/run_security_refresh.py']='old-driver'
        inventory=self.root/'source-inventory.json'
        inventory.write_text(json.dumps(self.sources))
        self.digest=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
        (self.root/'report.json').write_text(json.dumps(dict(passed=False,
            stages=[dict(name='quickstart',passed=True)],source_inventory_sha256=self.digest(inventory))))
        self.records=[]
        for population in (20000,50000):
            record=dict(population=population,status='validated',tag='candidate:'+str(population),
                        image_id='sha256:'+str(population),runtime_jar_sha256=self.runtime)
            self.records.append(record)
            directory=self.root/'quickstart'/('browser-'+str(population));directory.mkdir(parents=True)
            (directory/'report.json').write_text(json.dumps(dict(status='passed',cleanup=True,
                image_id=record['image_id'],private_rpc_evidence=dict(closed=True,errors=[]))))
        (self.root/'quickstart/status.json').write_text(json.dumps(dict(status='passed',images=self.records)))
        self.client=Mock()
        self.client.images.get.side_effect=lambda tag:Mock(id=next(r['image_id'] for r in self.records if r['tag']==tag))
        self.release={'digest':self.digest,'archive_hash':Mock(return_value=self.runtime)}

    def reuse(self,sources=None):
        return reused_quickstart(self.root,sources or self.sources,self.client,self.release,self.runtime)

    def test_failed_outer_workflow_can_reuse_complete_unchanged_quickstart_proof(self):
        sources=copy.deepcopy(self.sources)
        sources['simpaths']['files']['deploy/acceptance/run_security_refresh.py']='new-resume-driver'
        quick,receipt=self.reuse(sources)
        self.assertEqual(quick['status'],'passed')
        self.assertEqual(set(receipt['browser_reports']),{'20000','50000'})
        self.assertEqual(receipt['tooling_changes'][0]['path'],'deploy/acceptance/run_security_refresh.py')
        self.assertEqual(self.client.containers.create.call_count,2)
        self.assertEqual(self.client.containers.create.return_value.remove.call_count,2)

    def test_runtime_source_or_input_edits_cannot_reuse_previous_acceptance(self):
        for name,path in (('core','runtime.py'),('frontend','runtime.py'),('simpaths','input/population.csv'),
                          ('simpaths','deploy/acceptance/run_browser_acceptance.py')):
            with self.subTest(repository=name,path=path):
                sources=copy.deepcopy(self.sources);sources[name]['files'][path]='changed'
                with self.assertRaises(ValueError):self.reuse(sources)
        self.client.containers.create.assert_not_called()

    def test_inventory_tampering_or_incomplete_browser_cleanup_is_rejected(self):
        path=self.root/'source-inventory.json'
        original=path.read_bytes();path.write_text('{}')
        with self.assertRaises(ValueError):self.reuse()
        path.write_bytes(original)
        browser=self.root/'quickstart/browser-20000/report.json'
        record=json.loads(browser.read_text());record['cleanup']=False
        browser.write_text(json.dumps(record))
        with self.assertRaises(ValueError):self.reuse()
        self.client.containers.create.assert_not_called()

    def test_changed_tag_or_runtime_inside_image_cannot_be_reused(self):
        self.client.images.get.side_effect=None;self.client.images.get.return_value.id='sha256:replacement'
        with self.assertRaises(ValueError):self.reuse()
        self.client.containers.create.assert_not_called()
        self.client.images.get.side_effect=lambda tag:Mock(id=next(r['image_id'] for r in self.records if r['tag']==tag))
        self.release['archive_hash'].return_value='different-runtime'
        with self.assertRaises(ValueError):self.reuse()
        self.client.containers.create.return_value.remove.assert_called_once()


class RetainedTrainingEvidence(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.digest=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
        self.sources={name:dict(files={'runtime.py':'unchanged'}) for name in ('core','simpaths','frontend')}
        inventory=self.root/'source-inventory.json';inventory.write_text(json.dumps(self.sources))
        self.runtime='runtime-checksum';self.image_id='sha256:'+'a'*64
        self.tag='simpaths-interactive:uk-training-security-original'
        self.report=dict(passed=False,source_inventory_sha256=self.digest(inventory),
            runtime_jar_sha256=self.runtime,stages=[dict(name='interactive-images',passed=False,
                command=['python','build_local_images.py','--tag-suffix','security-original'])])
        self.save_report()
        self.log=self.root/'interactive-images.log'
        self.log.write_text('#13 exporting to image\n#13 exporting manifest list '+self.image_id+
            '\n#13 naming to docker.io/library/'+self.tag+' done\n#13 DONE 75.3s\n'
            'Preparing to build user-data\nBuild stopped: insufficient space\n')
        self.client=Mock();self.client.images.get.return_value.id=self.image_id
        self.release={'digest':self.digest,'archive_hash':Mock(return_value=self.runtime),
                      'archive_content_hashes':Mock(return_value={'population.csv':'original-hash'})}

    def save_report(self):
        (self.root/'report.json').write_text(json.dumps(self.report))

    def reuse(self,sources=None):
        return reused_training(self.root,sources or self.sources,self.client,self.release,self.runtime)

    def test_completed_export_survives_second_image_failure_but_does_not_reuse_browser_acceptance(self):
        record,receipt=self.reuse()
        self.assertEqual(record['image_id'],self.image_id)
        self.assertEqual(record['tag'],self.tag)
        self.assertFalse(receipt['browser_acceptance_reused'])
        self.assertEqual(receipt['build_log_sha256'],self.digest(self.log))
        self.client.containers.create.return_value.remove.assert_called_once()

    def test_incomplete_ambiguous_or_wrong_export_cannot_be_reused(self):
        original=self.log.read_text()
        for log in (original.replace('#13 DONE 75.3s\n',''),
                    original.replace('#13 DONE 75.3s\n','')+'#13 DONE 10s\n',
                    original.replace(self.image_id,'not-a-digest'),
                    original.replace('Preparing to build user-data\n','')+original,
                    original.replace(self.tag,'other-image')):
            with self.subTest(log=log):
                self.log.write_text(log)
                with self.assertRaises(ValueError):self.reuse()
        self.client.images.get.assert_not_called()

    def test_sources_receipts_image_ids_and_runtime_must_still_match(self):
        changed=copy.deepcopy(self.sources);changed['core']['files']['runtime.py']='different'
        with self.assertRaises(ValueError):self.reuse(changed)
        self.client.images.get.assert_not_called()
        self.client.images.get.return_value.id='sha256:'+'b'*64
        with self.assertRaises(ValueError):self.reuse()
        self.client.containers.create.assert_not_called()
        self.client.images.get.return_value.id=self.image_id
        self.release['archive_hash'].return_value='different'
        with self.assertRaises(ValueError):self.reuse()
        self.client.containers.create.return_value.remove.assert_called_once()

    def test_successful_new_build_checkpoint_requires_stage_and_matching_input_hashes(self):
        self.report['stages']=[dict(name='interactive-training-image',passed=True)]
        self.report['interactive_builds']={'training':dict(tag=self.tag,image_id=self.image_id,
            runtime_jar_sha256=self.runtime,input_hashes={'population.csv':'original-hash'})}
        self.save_report();self.log.unlink()
        record,receipt=self.reuse()
        self.assertEqual(record['input_hashes'],{'population.csv':'original-hash'})
        self.assertNotIn('build_log_sha256',receipt)
        self.release['archive_content_hashes'].return_value={'population.csv':'different'}
        with self.assertRaises(ValueError):self.reuse()
        self.report['stages'][0]['passed']=False;self.save_report()
        with self.assertRaises(ValueError):training_build_record(self.root)


class RetainedInteractiveEvidence(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.runtime='runtime-checksum'
        self.digest=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
        self.sources={name:dict(files={'runtime.py':'unchanged'}) for name in ('core','simpaths','frontend')}
        inventory=self.root/'source-inventory.json';inventory.write_text(json.dumps(self.sources))
        self.records={profile:dict(tag='candidate:'+profile,image_id='sha256:'+profile,
            runtime_jar_sha256=self.runtime,input_hashes={'input.csv':'original'}) for profile in ('training','user-data')}
        self.report=dict(passed=False,source_inventory_sha256=self.digest(inventory),
            runtime_jar_sha256=self.runtime,interactive_builds=self.records,
            reused_training={'directory':'original-training-build'},
            stages=[dict(name='interactive-user-data-image',passed=True)])
        (self.root/'report.json').write_text(json.dumps(self.report))
        self.previous=dict(passed=False,cleanup=True,private_rpc=dict(closed=True,errors=[]),
            images={'owner-'+profile:record['image_id'] for profile,record in self.records.items()},checks=[])
        for mode in ('none','population','tax','both'):
            name='training-'+mode
            self.previous['checks'].append(dict(name=name,passed=True))
            directory=self.root/'interactive'/name;directory.mkdir(parents=True)
            (directory/'report.json').write_text(json.dumps(dict(status='passed',prepare=mode,page_errors=[])))
        self.save_previous()
        self.client=Mock()
        self.client.images.get.side_effect=lambda tag:Mock(id=next(r['image_id'] for r in self.records.values() if r['tag']==tag))
        self.release={'digest':self.digest,'archive_hash':Mock(return_value=self.runtime),
                      'archive_content_hashes':Mock(return_value={'input.csv':'original'})}

    def save_previous(self):
        (self.root/'interactive/report.json').write_text(json.dumps(self.previous))

    def reuse(self,sources=None):
        return reused_interactive(self.root,sources or self.sources,self.client,self.release,self.runtime)

    def test_failed_user_data_can_reuse_four_training_passes_and_both_unchanged_images(self):
        records,receipt=self.reuse()
        self.assertEqual(records,self.records)
        self.assertEqual(len(receipt['training_checks']),4)
        self.assertFalse(receipt['user_data_acceptance_reused'])
        self.assertTrue(all(check['reused'] for check in receipt['training_checks']))
        self.assertEqual(self.client.containers.create.return_value.remove.call_count,2)

    def test_missing_failed_or_duplicate_training_modes_and_unknown_cleanup_are_rejected(self):
        original=copy.deepcopy(self.previous)
        invalid=[]
        item=copy.deepcopy(original);item['checks'].pop();invalid.append(item)
        item=copy.deepcopy(original);item['checks'][0]['passed']=False;invalid.append(item)
        item=copy.deepcopy(original);item['checks'].append(item['checks'][0]);invalid.append(item)
        item=copy.deepcopy(original);item['cleanup']=False;invalid.append(item)
        item=copy.deepcopy(original);item['private_rpc']['closed']=False;invalid.append(item)
        item=copy.deepcopy(original);item['private_rpc']['errors']=['Failure'];invalid.append(item)
        for item in invalid:
            self.previous=item;self.save_previous()
            with self.subTest(record=item):
                with self.assertRaises(ValueError):self.reuse()
        self.client.images.get.assert_not_called()

    def test_browser_failure_or_mismatched_profile_identity_cannot_be_reused(self):
        path=self.root/'interactive/training-none/report.json'
        for key,value in (('status','failed'),('cleanup_error','failed'),('page_errors',['error']),
                          ('artifact_errors',{'trace.zip':'missing'}),('prepare','both')):
            record=dict(status='passed',prepare='none',page_errors=[]);record[key]=value
            path.write_text(json.dumps(record))
            with self.subTest(field=key):
                with self.assertRaises(ValueError):self.reuse()
        path.write_text(json.dumps(dict(status='passed',prepare='none',page_errors=[])))
        self.previous['images']['owner-training']='sha256:other';self.save_previous()
        with self.assertRaises(ValueError):self.reuse()
        self.client.images.get.assert_not_called()

    def test_changed_sources_images_runtime_and_input_hashes_are_rejected(self):
        changed=copy.deepcopy(self.sources);changed['frontend']['files']['runtime.py']='changed'
        with self.assertRaises(ValueError):self.reuse(changed)
        self.client.images.get.assert_not_called()
        self.client.images.get.side_effect=None;self.client.images.get.return_value.id='sha256:other'
        with self.assertRaises(ValueError):self.reuse()
        self.client.containers.create.assert_not_called()
        self.client.images.get.side_effect=lambda tag:Mock(id=next(r['image_id'] for r in self.records.values() if r['tag']==tag))
        self.release['archive_hash'].return_value='changed'
        with self.assertRaises(ValueError):self.reuse()
        self.client.containers.create.return_value.remove.assert_called_once()
        self.release['archive_hash'].return_value=self.runtime
        self.release['archive_content_hashes'].return_value={'input.csv':'changed'}
        with self.assertRaises(ValueError):self.reuse()

    def test_partial_image_builds_cannot_start_browser_only_resume(self):
        del self.report['interactive_builds']['user-data']
        (self.root/'report.json').write_text(json.dumps(self.report))
        with self.assertRaises(ValueError):interactive_resume_record(self.root)


if __name__ == '__main__':unittest.main()
