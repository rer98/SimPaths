"""(C) Copyright 2026, by Ross Richardson
Public training provenance, immutable release binding and safe image-source copies.
Fictional files exercise the normal preparation reader; no Docker or simulations.
@author ross richardson
"""
from copy import deepcopy
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy._workflow import frontend_path
from .artifacts import ArtifactError, digest, fingerprint
from .browser_model import BrowserModel
from .local_web import register_training
from .prepared_dataset import verify_snapshot
from .public_training import (FORMAT, NAME, SCHEDULE, _copy_member, certify,
                              is_public_receipt, prepare_example, source_manifest)
from .releases import CONTRACT
from .resource_policy import DEFAULT_POLICY
from . import test_prepare_inputs
from .workload_envelope import extended_resource_policy


class PublicTrainingTests(unittest.TestCase):
    def setUp(self):
        self.f=test_prepare_inputs.InputPreparationTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.uploads={}
        for name in ('population_initial_UK_2019.csv','uk_2015_std.txt','uk_2019_std.txt','uk_2020_std.txt'):
            path=self.f.root/name;path.write_bytes(('fictional public example '+name).encode())
            self.f.uploads[name]=path
        self.f.request=dict(year=2019,schedule=deepcopy(SCHEDULE))
        self.manifest=dict(format=FORMAT,repository='https://github.com/simpaths/SimPaths',
            files={name:fingerprint(path) for name,path in self.f.uploads.items()})
        self.manifest['paths']={name:'input/'+('InitialPopulations' if name.endswith('.csv') else 'EUROMODoutput')+'/training/'+name
                                for name in self.f.uploads}
        patched=patch('deploy.multirun.public_training.source_manifest',return_value=self.manifest)
        patched.start();self.addCleanup(patched.stop)
        self.release_id='release-'+'b'*64
        self.runner=self.f.root/'run.sh';self.runner.write_text('# fictional runner\n')
        self.release=dict(image='sha256:'+'a'*64,jar=self.f.jar,defaults=self.f.defaults,
            runner=self.runner,contract=CONTRACT,resource_policy=extended_resource_policy(DEFAULT_POLICY),
            identity=dict(model=fingerprint(self.f.jar),defaults={'parameters.xlsx':fingerprint(self.f.defaults/'parameters.xlsx')},
                          runner=fingerprint(self.runner)))

    def prepared(self):
        receipt=self.f.prepare()
        fingerprint(self.runner,self.f.output/'run.sh')
        receipt['identity']['release']=dict(id=self.release_id,contract=CONTRACT,
            resource_policy=self.release['resource_policy'],runner=fingerprint(self.runner))
        receipt['sha256']=digest(receipt['identity']);receipt['revision']='inputs-'+receipt['sha256']
        return receipt

    def test_certificate_requires_all_four_unchanged_public_sources_and_exact_schedule(self):
        original=self.prepared()
        self.assertFalse(is_public_receipt(original))
        receipt=certify(original)
        self.assertTrue(is_public_receipt(receipt))
        self.assertNotIn('public_training',original['identity'])
        for changed in ('upload','prepared','schedule','release','marker'):
            bad=deepcopy(receipt)
            identity=bad['identity']
            if changed=='upload':identity['sources']['uploads']['uk_2020_std.txt']['sha256']='c'*64
            if changed=='prepared':identity['prepared']['EUROMODoutput/uk_2020_std.txt']['sha256']='c'*64
            if changed=='schedule':identity['selection']['schedule'][0][3]='Different selection'
            if changed=='release':identity.pop('release')
            if changed=='marker':identity['public_training']['files'].pop('uk_2020_std.txt')
            bad['sha256']=digest(identity);bad['revision']='inputs-'+bad['sha256']
            with self.subTest(changed=changed),self.assertRaises(ArtifactError):is_public_receipt(bad)

    def test_generic_provider_inputs_cannot_be_registered_as_public(self):
        receipt=self.prepared()
        (self.f.output/'receipt.json').write_text(json.dumps(receipt))
        datasets=SimpleNamespace(queue=Mock())
        with self.assertRaisesRegex(ArtifactError,'verified public training'):
            register_training(datasets,Mock(),self.f.output)
        datasets.queue._transaction.assert_not_called()

    def test_configurable_example_has_editable_population_and_bound_new_release(self):
        receipt=certify(self.prepared());(self.f.output/'receipt.json').write_text(json.dumps(receipt))
        verify_snapshot(self.f.output,receipt)
        model=BrowserModel({self.release_id:self.release},max_repetitions=10)
        described=model.describe_dataset(dict(dataset_id=receipt['revision'],location=str(self.f.output),
            prepared_fingerprint=receipt['sha256'],model_digest=self.release['image']))
        self.assertEqual(described['name'],NAME)
        self.assertEqual(described['locked'],['start_year'])
        self.assertEqual(described['model_release'],self.release_id)
        self.assertEqual(described['values'],dict(start_year=2019,population=20000,end_year=2026))

    def test_importer_uses_normal_preparation_then_reuses_only_matching_verified_output(self):
        receipt=self.prepared()
        # Exercise publication/binding after the ordinary native preparation boundary.
        receipt['identity'].pop('release')
        receipt['sha256']=digest(receipt['identity']);receipt['revision']='inputs-'+receipt['sha256']
        (self.f.output/'run.sh').unlink()
        target=self.f.root/'published'
        def prepare(*args):
            self.f.output.rename(args[5])
            return receipt
        with patch('deploy.multirun.public_training.copy_sources',return_value=self.f.uploads) as sources, \
             patch('deploy.multirun.prepare_inputs.prepare',side_effect=prepare) as native:
            saved=prepare_example(self.release_id,self.release,target,frontend_path())
        self.assertTrue(is_public_receipt(saved));verify_snapshot(target,saved)
        self.assertEqual(native.call_args.args[2],dict(year=2019,schedule=SCHEDULE))
        self.assertEqual(sources.call_args.args[0],self.release['image'])
        with patch('deploy.multirun.public_training.copy_sources') as sources:
            self.assertEqual(prepare_example(self.release_id,self.release,target,frontend_path()),saved)
            sources.assert_not_called()
        with self.assertRaisesRegex(ArtifactError,'different existing'):
            prepare_example('release-'+'c'*64,self.release,target,frontend_path())

    def test_failed_preparation_never_publishes_the_stable_example_path(self):
        target=self.f.root/'unpublished'
        def fail(*args):
            staging=args[5];staging.mkdir(mode=0o700)
            (staging/'native-diagnostic.txt').write_text('fictional stopped validation')
            raise ArtifactError('Invalid fictional input')
        with patch('deploy.multirun.public_training.copy_sources',return_value=self.f.uploads), \
             patch('deploy.multirun.prepare_inputs.prepare',side_effect=fail):
            with self.assertRaises(ArtifactError):
                prepare_example(self.release_id,self.release,target,frontend_path())
        self.assertFalse(target.exists())
        staged=list(self.f.root.glob('.pending-unpublished-*'))
        self.assertEqual(len(staged),1)
        self.assertTrue((staged[0]/'native-diagnostic.txt').exists())


class PublicArchiveTests(unittest.TestCase):
    def test_reviewed_manifest_names_only_the_four_public_repository_files(self):
        manifest=source_manifest()
        self.assertEqual(len(manifest['files']),4)
        self.assertTrue(all('/training/' in name for name in manifest['paths'].values()))
        self.assertEqual(set(manifest['files']),set(manifest['paths']))

    def archive(self,entries):
        value=io.BytesIO()
        with tarfile.open(fileobj=value,mode='w') as archive:
            for name,body,kind in entries:
                member=tarfile.TarInfo(name);member.size=len(body);member.type=kind
                if kind==tarfile.SYMTYPE:member.linkname='/etc/passwd';member.size=0
                archive.addfile(member,io.BytesIO(body))
        value.seek(0);return value

    def test_only_exact_ordinary_image_file_is_copied(self):
        import tempfile
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);expected=root/'expected';expected.write_bytes(b'public')
            result=root/'file.txt'
            _copy_member(self.archive([('file.txt',b'public',tarfile.REGTYPE)]),result,fingerprint(expected))
            self.assertEqual(result.read_bytes(),b'public')
            result.unlink()
            for entries in ([('../file.txt',b'public',tarfile.REGTYPE)],
                    [('file.txt',b'public',tarfile.SYMTYPE)],
                    [('file.txt',b'secret',tarfile.REGTYPE)],
                    [('file.txt',b'public',tarfile.REGTYPE),('extra',b'public',tarfile.REGTYPE)],[]):
                with self.subTest(entries=entries),self.assertRaises(ArtifactError):
                    _copy_member(self.archive(entries),result,fingerprint(expected))
                result.unlink(missing_ok=True)


if __name__=='__main__':unittest.main()
