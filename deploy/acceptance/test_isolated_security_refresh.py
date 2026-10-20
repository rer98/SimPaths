"""(C) Copyright 2026, by Ross Richardson

Verify isolated candidate clones, protected artifacts and owned-process cleanup.
Tiny local Git fixtures require no Docker, network or scientific simulation.
@author ross richardson
"""
from pathlib import Path
import json
import errno
import shutil
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from deploy.acceptance import run_isolated_security_refresh as candidate
from deploy.acceptance import isolated_refresh_storage as storage


class IsolatedSecurityRefreshTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)

    def repository(self,name):
        root=self.root/name;root.mkdir()
        def git(*args):
            return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL,text=True).strip()
        git('init','-q')
        for file in ('pom.xml','input/DatabaseCountryYear.xlsx','input/EUROMODpolicySchedule.xlsx'):
            path=root/file;path.parent.mkdir(exist_ok=True);path.write_text('committed fixture\n')
        git('add','.')
        git('-c','user.name=Fictional test','-c','user.email=test@example.org','commit','-qm','fixture')
        return root

    def test_real_clones_use_selected_committed_files_and_leave_originals_unchanged(self):
        repositories={name:self.repository(name) for name in ('core','model','frontend')}
        workbook=repositories['model']/'input/DatabaseCountryYear.xlsx';workbook.write_text('local uncommitted workbook\n')
        selected=candidate.selected_sources(repositories)
        before={name:candidate.git(path,'status','--porcelain') for name,path in repositories.items()}
        with (self.root/'clone.log').open('w') as log:
            clones=candidate.clone_sources(selected,self.root/'clones',log)
        for name,path in clones.items():
            self.assertEqual(candidate.git(path,'rev-parse','HEAD'),selected[name]['commit'])
            self.assertEqual(candidate.git(path,'status','--porcelain'),'')
            self.assertEqual(candidate.git(repositories[name],'status','--porcelain'),before[name])
        self.assertEqual((clones['model']/'input/DatabaseCountryYear.xlsx').read_text(),'committed fixture\n')
        self.assertEqual(workbook.read_text(),'local uncommitted workbook\n')
        (clones['model']/'singlerun.jar').write_text('new candidate only')
        self.assertFalse((repositories['model']/'singlerun.jar').exists())

    def test_tracked_runtime_source_changes_require_a_commit(self):
        root=self.repository('model')
        (root/'pom.xml').write_text('uncommitted runtime dependency')
        with self.assertRaisesRegex(ValueError,'Commit tracked source'):
            candidate.selected_sources(dict(model=root))

    def test_protected_workbooks_jars_and_catalogue_detect_changes(self):
        repositories=dict(model=self.root/'model',frontend=self.root/'frontend')
        for root,names in [(repositories['model'],('singlerun.jar','multirun.jar','input/DatabaseCountryYear.xlsx','input/EUROMODpolicySchedule.xlsx')),
                           (repositories['frontend'],('deploy/simpaths/models.json',))]:
            for name in names:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original fixture')
        receipt=candidate.protected_files(repositories)
        candidate.verify_protected(receipt)
        (repositories['model']/'multirun.jar').write_text('changed')
        with self.assertRaises(RuntimeError):candidate.verify_protected(receipt)

    def test_acceptance_is_a_sibling_so_the_heavy_work_lock_is_shared(self):
        clones={name:self.root/name for name in ('core','model','frontend')}
        output=self.root/'candidate'
        command,acceptance=candidate.acceptance_command('/candidate/venv/bin/python',clones,output,self.root/'maven',prune=True)
        self.assertEqual(acceptance.parent,output.parent)
        self.assertEqual(acceptance.name,'candidate-acceptance')
        self.assertEqual(command[0],'/candidate/venv/bin/python')
        self.assertIn(str(clones['model']/'deploy/acceptance/run_security_refresh.py'),command)
        self.assertIn('--prune-build-cache',command)
        self.assertNotIn('--promote',command)

    def test_nonzero_acceptance_is_never_reported_as_success(self):
        process=Mock();process.wait.return_value=1
        with patch.object(candidate.subprocess,'Popen',return_value=process) as start:
            with self.assertRaises(subprocess.CalledProcessError):candidate.run_owned(['proof'],None,environment={})
        self.assertTrue(start.call_args.kwargs['start_new_session'])

    def test_interruption_waits_for_owned_driver_cleanup(self):
        process=Mock();process.wait.side_effect=[KeyboardInterrupt(),0];process.poll.return_value=None
        with patch.object(candidate.subprocess,'Popen',return_value=process),patch.object(candidate.os,'killpg') as kill:
            with self.assertRaises(KeyboardInterrupt):candidate.run_owned(['proof'],None,environment={})
        process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(process.wait.call_args.kwargs['timeout'],900)
        kill.assert_not_called()

    def test_stuck_owned_driver_is_killed_and_reaped(self):
        process=Mock(pid=12345);process.poll.return_value=None
        process.wait.side_effect=[KeyboardInterrupt(),subprocess.TimeoutExpired('proof',900),-9]
        with patch.object(candidate.subprocess,'Popen',return_value=process),patch.object(candidate.os,'killpg') as kill:
            with self.assertRaises(KeyboardInterrupt):candidate.run_owned(['proof'],None,environment={})
        kill.assert_called_once_with(12345,signal.SIGKILL)
        self.assertEqual(process.wait.call_args.kwargs['timeout'],30)

    def test_missing_bases_are_rejected_before_an_expensive_build(self):
        with patch.object(candidate,'quickstart_image',return_value='missing:base'), \
             patch.object(candidate.subprocess,'check_output',side_effect=subprocess.CalledProcessError(1,'inspect')):
            with self.assertRaisesRegex(RuntimeError,'prepare-quickstart'):
                candidate.existing_bases(self.root)

    def test_base_override_changes_only_private_catalogue_image_fields(self):
        path=self.root/'deploy/simpaths/models.json';path.parent.mkdir(parents=True)
        original=dict(models=[dict(id='simpaths-quickstart-'+str(p),name='fixture',
            deployment=dict(image='old:'+str(p),memory='5Gi')) for p in (20000,50000)])
        path.write_text(json.dumps(original))
        bases={str(p):dict(tag='private:'+str(p),image_id='sha256:'+'1'*64) for p in (20000,50000)}
        receipt=candidate.set_private_bases(self.root,bases)
        result=json.loads(path.read_text())
        for index,population in enumerate((20000,50000)):
            expected=original['models'][index]
            expected['deployment']['image']='private:'+str(population)
            self.assertEqual(result['models'][index],expected)
        self.assertNotEqual(receipt['before_sha256'],receipt['after_sha256'])

    def fresh_base_fixture(self,*,corrupt=False,retry=False):
        clones={name:self.root/name for name in ('core','model','frontend')}
        for root in clones.values():root.mkdir()
        output=self.root/'evidence';output.mkdir()
        template=clones['model']/'deploy/web-quickstart/Dockerfile';template.parent.mkdir(parents=True)
        template.write_text((candidate.ROOT/'deploy/web-quickstart/Dockerfile').read_text())
        state={};commands=[]
        def run(command,log,**settings):
            commands.append((command,settings))
            if command[0]=='mvn':
                for name in ('singlerun.jar','multirun.jar'):(clones['model']/name).write_text('candidate runtime')
                log.write('BUILD SUCCESS\n')
            elif 'prepare_profile.py' in command[1]:
                directory=Path(command[command.index('--output')+1]);directory.mkdir()
                for name in ('work','loading-check','package/input'):(directory/name).mkdir(parents=True)
                (directory/'status.json').write_text('{"status":"complete"}')
                (directory/'package/input/input.mv.db').write_text('prepared database')
                (directory/'package/profile.json').write_text(json.dumps(dict(fresh_jvm_loading_verified=True,
                    jar_sha256=candidate.digest(clones['model']/'singlerun.jar'),
                    profile=dict(requested_population=int(command[command.index('--population')+1])))))
                (directory/'package/checksums.json').write_text(json.dumps({str(path.relative_to(directory/'package')):
                    candidate.digest(path) for path in (directory/'package').rglob('*') if path.is_file()}))
                state['profile']=candidate.digest(directory/'package/profile.json')
                state['inputs']={'input/input.mv.db':candidate.digest(directory/'package/input/input.mv.db')}
            elif 'package_image.py' in command[1]:
                package=Path(command[command.index('--package')+1])
                context=Path(command[command.index('--output')+1]);shutil.copytree(package,context)
                state['context']=context
        def hash_file(container,path):
            if corrupt:return 'wrong'
            return candidate.digest(clones['model']/'singlerun.jar') if path=='/app/app.jar' else state['profile']
        def hashes(container,path):
            return state['inputs']
        verifier=dict(archive_hash=hash_file,archive_content_hashes=hashes,archive_file=lambda *args:b'{}',
                      verify_query_migration=Mock())
        client=Mock();client.images.get.return_value.id='sha256:'+'1'*64
        with patch.object(candidate,'run_owned',side_effect=run), \
             patch.object(candidate.runpy,'run_path',return_value=verifier), \
             patch('docker.from_env',return_value=client), \
             patch.object(candidate.shutil,'disk_usage',return_value=Mock(free=20*1024**3)):
            if corrupt:
                with self.assertRaisesRegex(RuntimeError,'differs'):
                    candidate.prepare_bases('/candidate/python',clones,output,self.root/'maven')
                if retry:
                    corrupt=False;commands.clear()
                    bases=candidate.prepare_bases('/candidate/python',clones,output,self.root/'maven',resume=True)
                    self.assertEqual(set(bases),{'20000','50000'})
            else:
                bases=candidate.prepare_bases('/candidate/python',clones,output,self.root/'maven')
                self.assertEqual(set(bases),{'20000','50000'})
        return output,client,commands

    def test_fresh_bases_use_private_builds_and_retire_only_verified_owned_bulk(self):
        output,client,commands=self.fresh_base_fixture()
        self.assertEqual(client.containers.create.return_value.remove.call_count,2)
        for population in (20000,50000):
            root=output/'prepared-bases'/str(population)
            self.assertTrue((root/'profile.json').is_file())
            self.assertTrue((root/'checksums.json').is_file())
            for name in ('work','loading-check','package'):self.assertFalse((root/name).exists())
        builds=[settings['cwd'] for command,settings in commands if command[0]=='mvn']
        self.assertEqual(builds,[self.root/'core',self.root/'model'])
        self.assertFalse(any(command[:3]==['docker','builder','prune'] for command,_ in commands))

    def test_failed_base_verification_preserves_reusable_context_and_removes_temporary_container(self):
        output,client,commands=self.fresh_base_fixture(corrupt=True)
        self.assertTrue((output/'prepared-bases/20000/context/input/input.mv.db').is_file())
        self.assertTrue((output/'prepared-bases/20000/context-receipt.json').is_file())
        self.assertFalse((output/'prepared-bases/images.json').exists())
        client.containers.create.return_value.remove.assert_called_once()

    def test_retry_reuses_java_and_completed_preparation_instead_of_running_them_again(self):
        output,client,commands=self.fresh_base_fixture(corrupt=True,retry=True)
        self.assertFalse(any(command[0]=='mvn' for command,_ in commands))
        preparation=[command for command,_ in commands if len(command)>1 and 'prepare_profile.py' in command[1]]
        self.assertEqual(len(preparation),1)
        self.assertEqual(preparation[0][preparation[0].index('--population')+1],'50000')
        self.assertTrue((output/'prepared-bases/images.json').is_file())

    def test_atomic_checkpoint_does_not_truncate_previous_record_on_a_full_disk(self):
        path=self.root/'report.json';path.write_text('{"previous":true}\n')
        with patch.object(storage.os,'replace',side_effect=OSError(errno.ENOSPC,'full')):
            with self.assertRaises(OSError):storage.checkpoint(path,dict(next=True))
        self.assertEqual(json.loads(path.read_text()),dict(previous=True))
        self.assertEqual(list(self.root.glob('.report.json-*')),[])

    def test_emergency_reserve_allows_one_atomic_checkpoint_retry(self):
        path=self.root/'report.json';storage.checkpoint(path,dict(previous=True))
        reserve=storage.reserve_reports(self.root)
        commit=storage._commit_json;calls=[]
        def fail_once(target,payload):
            calls.append(json.loads(path.read_text()))
            if len(calls)==1:raise OSError(errno.ENOSPC,'full')
            self.assertFalse(reserve.exists())
            return commit(target,payload)
        with patch.object(storage,'_commit_json',side_effect=fail_once):
            storage.checkpoint(path,dict(next=True),reserve=reserve)
        self.assertEqual(calls,[dict(previous=True),dict(previous=True)])
        self.assertEqual(json.loads(path.read_text()),dict(next=True))

    def test_space_monitor_interrupts_and_joins_owned_child_before_reporting_failure(self):
        process=Mock();process.poll.return_value=None
        process.wait.side_effect=[subprocess.TimeoutExpired('build',1),-2]
        with patch.object(candidate.subprocess,'Popen',return_value=process), \
             patch.object(candidate.shutil,'disk_usage',side_effect=[Mock(free=10*storage.GIB),
                 Mock(free=10*storage.GIB),Mock(free=2*storage.GIB)]):
            with self.assertRaisesRegex(RuntimeError,'less than 3 GiB'):
                candidate.run_owned(['build'],None,environment=dict(TMPDIR=str(self.root)))
        process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual(process.wait.call_args.kwargs['timeout'],900)

    def test_compact_recipe_keeps_runtime_security_and_publishes_one_input_layer(self):
        original=(candidate.ROOT/'deploy/web-quickstart/Dockerfile').read_text()
        result=storage.compact_dockerfile(original)
        final=result.rsplit('FROM refresh_dependencies\n',1)[1]
        self.assertEqual(final.count('/app/input/'),2)
        self.assertNotIn('DatabaseQueryAccess',final)
        self.assertIn('--chown=10001:10001',final)
        self.assertIn('--chown=0:0',final)
        self.assertIn('chmod 0444',final)
        self.assertIn('USER 10001:10001',final)
        self.assertTrue(final.endswith(original[original.index('RUN mkdir -p output'):]))
        self.assertIn('apt-get update',result)
        self.assertEqual(result.count('DatabaseQueryAccess'),1)
        recovered=storage.compact_dockerfile(original,'simpaths-quickstart:verified-source')
        self.assertNotIn('DatabaseQueryAccess',recovered)
        self.assertIn('FROM simpaths-quickstart:verified-source AS refresh_prepared',recovered)

    def test_only_completed_unambiguous_native_exports_can_be_recovered(self):
        path=self.root/'base.log';identifier='sha256:'+'1'*64
        text=('#13 exporting manifest list '+identifier+' 0.0s done\n'
            '#13 naming to docker.io/library/simpaths-quickstart:security-base-20000-20261020-181212 done\n')
        path.write_text(text)
        with self.assertRaisesRegex(ValueError,'did not finish'):candidate.completed_export(path,20000)
        path.write_text(text+'#13 DONE 120.0s\n')
        self.assertEqual(candidate.completed_export(path,20000)['image_id'],identifier)
        path.write_text(text+text+'#13 DONE 120.0s\n')
        with self.assertRaisesRegex(ValueError,'Ambiguous'):candidate.completed_export(path,20000)

    def test_real_quickstart_receipts_can_be_reused_by_existing_refresh_guards(self):
        from deploy.acceptance.run_security_refresh import reused_quickstart
        clones={name:self.root/name for name in ('core','model','frontend')}
        for path in clones.values():path.mkdir()
        (clones['model']/'singlerun.jar').write_text('frozen runtime')
        output=self.root/'candidate';output.mkdir()
        runtime=candidate.digest(clones['model']/'singlerun.jar')
        sources={key:dict(revision='a'*40,worktree='',files={'source.txt':'b'*64})
                 for key in ('core','simpaths','frontend')}
        def inventory(path,**kwargs):return sources['simpaths' if path==clones['model'] else path.name]
        bases={str(population):dict(tag='candidate:'+str(population),image_id='sha256:'+digit*64,
            runtime_jar_sha256=runtime,profile_sha256='c'*64) for population,digit in ((20000,'1'),(50000,'2'))}
        verifier=dict(source_inventory=inventory,digest=candidate.digest,archive_hash=lambda *args:runtime)
        def browser(command,log,**kwargs):
            root=Path(command[command.index('--output')+1]);root.mkdir()
            population=command[command.index('--population')+1]
            (root/'report.json').write_text(json.dumps(dict(status='passed',cleanup=True,
                image_id=bases[population]['image_id'],private_rpc_evidence=dict(closed=True,errors=[]))))
        with patch.object(candidate.runpy,'run_path',return_value=verifier), \
             patch.object(candidate,'run_owned',side_effect=browser) as run, \
             patch.object(candidate.shutil,'disk_usage',return_value=Mock(free=20*storage.GIB)):
            qualified=candidate.qualify_bases('/candidate/python',clones,output,bases)
            self.assertEqual(run.call_count,2)
            run.reset_mock()
            self.assertEqual(candidate.qualify_bases('/candidate/python',clones,output,bases),qualified)
            run.assert_not_called()
        client=Mock()
        client.images.get.side_effect=lambda tag:Mock(id=next(item['image_id'] for item in bases.values() if item['tag']==tag))
        quick,receipt=reused_quickstart(qualified,sources,client,verifier,runtime)
        self.assertEqual(quick['status'],'passed')
        self.assertEqual(set(receipt['browser_reports']),{'20000','50000'})
        command,_=candidate.acceptance_command('/python',clones,output,self.root/'maven',quickstart=qualified)
        self.assertEqual(command[command.index('--resume-from')+1],str(qualified))

    def test_resume_accepts_only_the_recorded_private_catalogue_image_override(self):
        repositories={name:self.repository(name) for name in ('core','model','frontend')}
        frontend=repositories['frontend'];path=frontend/'deploy/simpaths/models.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(models=[dict(id='simpaths-quickstart-'+str(population),
            deployment=dict(image='old:'+str(population),memory='5Gi')) for population in (20000,50000)])))
        candidate.git(frontend,'add','deploy/simpaths/models.json')
        candidate.git(frontend,'-c','user.name=Fictional test','-c','user.email=test@example.org','commit','-qm','catalogue fixture')
        selected=candidate.selected_sources(repositories)
        with (self.root/'clone.log').open('w') as log:
            clones=candidate.clone_sources(selected,self.root/'sources',log)
        bases={str(population):dict(tag='private:'+str(population)) for population in (20000,50000)}
        candidate.set_private_bases(clones['frontend'],bases)
        self.assertEqual(candidate.resume_clones(selected,self.root),clones)
        path=clones['frontend']/'deploy/simpaths/models.json'
        altered=json.loads(path.read_text());altered['models'][0]['deployment']['memory']='99Gi'
        path.write_text(json.dumps(altered))
        with self.assertRaisesRegex(ValueError,'Only private Quick Start'):
            candidate.resume_clones(selected,self.root)

    def test_build_guard_counts_logical_sparse_database_size_and_export_headroom(self):
        with (self.root/'input.mv.db').open('wb') as stream:stream.truncate(3*storage.GIB)
        self.assertEqual(storage.required_build_space(self.root),12*storage.GIB)

    def test_preparation_failure_records_preservation_and_never_starts_acceptance(self):
        repositories={name:self.repository(name) for name in ('core','model','frontend')}
        for name in ('singlerun.jar','multirun.jar'):(repositories['model']/name).write_text('benchmark runtime')
        catalogue=repositories['frontend']/'deploy/simpaths/models.json';catalogue.parent.mkdir(parents=True)
        catalogue.write_text('{"models":[]}')
        maven=self.root/'maven';maven.mkdir()
        output=self.root/'failed'
        args=['--frontend',str(repositories['frontend']),'--core',str(repositories['core']),
              '--maven-repository',str(maven),'--output',str(output),'--prepare-quickstart']
        native_run=candidate.subprocess.run
        def only_docker(command,**kwargs):
            return Mock(returncode=0) if command[0]=='docker' else native_run(command,**kwargs)
        with patch.object(candidate,'ROOT',repositories['model']), \
             patch.object(candidate.tempfile,'gettempdir',return_value=str(self.root)), \
             patch.object(candidate.subprocess,'run',side_effect=only_docker), \
             patch.object(candidate.shutil,'disk_usage',return_value=Mock(free=20*1024**3)), \
             patch.object(candidate,'clone_sources',side_effect=RuntimeError('fictional failure')), \
             patch.object(candidate,'run_owned') as run:
            self.assertEqual(candidate.main(args),1)
        report=json.loads((output/'report.json').read_text())
        self.assertFalse(report['passed'])
        self.assertTrue(report['originals_unchanged'])
        self.assertEqual(report['error_type'],'RuntimeError')
        self.assertIn('finished_at',report)
        run.assert_not_called()


if __name__=='__main__':unittest.main()
