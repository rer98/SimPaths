"""(C) Copyright 2026, by Ross Richardson

Release upgrades, legacy identity, immutable policies and interrupted publication.
Uses fictional files; model execution and Docker are not needed.
@author ross richardson
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from .artifacts import ArtifactError, fingerprint, inventory, write_json
from .local_web import frozen_release
from .resource_policy import DEFAULT_POLICY, LEGACY_POLICY, check_policy, simulation_resources, simulation_storage
from . import releases
from .releases import ReleaseRegistry

IMAGE_A = 'sha256:'+'a'*64
IMAGE_B = 'sha256:'+'b'*64


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.state=self.root/'state'
        self.state.mkdir(mode=0o700)
        self.registry=ReleaseRegistry(self.state)
        self.jar=self.root/'model.jar'; self.jar.write_bytes(b'fictional model A')
        self.defaults=self.root/'defaults'; self.defaults.mkdir()
        (self.defaults/'scenario_CPI.xlsx').write_bytes(b'default workbook A')
        (self.defaults/'DatabaseCountryYear.xlsx').write_bytes(b'generated schedule excluded')

    def register(self, **kwargs):
        return self.registry.register(**dict(image=IMAGE_A,name='Version A',jar=self.jar,
                                             defaults=self.defaults,**kwargs))

    def test_new_default_retains_old_jar_workbooks_image_and_policy(self):
        first=self.register()
        before=self.registry.load()[first]
        model=fingerprint(before['jar']); defaults=inventory(before['defaults'])
        self.jar.write_bytes(b'fictional model B')
        (self.defaults/'scenario_CPI.xlsx').write_bytes(b'default workbook B')
        policy=deepcopy(DEFAULT_POLICY); policy['simulation']['storage']['setup_mib']=11264
        second=self.registry.register(image=IMAGE_B,name='Version B',jar=self.jar,defaults=self.defaults,
                                      policy=policy,make_default=True)
        loaded=ReleaseRegistry(self.state).load(expected_image=IMAGE_B)
        self.assertEqual(list(loaded),[second,first])
        self.assertEqual(fingerprint(loaded[first]['jar']),model)
        self.assertEqual(inventory(loaded[first]['defaults']),defaults)
        self.assertEqual(loaded[first]['image'],IMAGE_A)
        self.assertEqual(loaded[first]['resource_policy'],DEFAULT_POLICY)
        self.assertEqual(loaded[second]['resource_policy'],policy)
        self.assertNotIn('DatabaseCountryYear.xlsx',inventory(loaded[first]['defaults']))

    def test_policy_or_workbook_change_is_a_distinct_release_even_with_identical_jar(self):
        retained_policy=deepcopy(DEFAULT_POLICY)
        retained_policy['simulation']['storage']['per_repetition_mib']=512
        first=self.register(policy=retained_policy)
        second=self.register()
        (self.defaults/'scenario_CPI.xlsx').write_bytes(b'changed defaults')
        third=self.register()
        self.assertEqual(len({first,second,third}),3)
        loaded=ReleaseRegistry(self.state).load()
        self.assertEqual(loaded[first]['resource_policy'],retained_policy)
        self.assertEqual(simulation_storage(loaded[first]['resource_policy'],repetitions=12)['storage_mib'],10240)
        self.assertEqual(simulation_storage(loaded[second]['resource_policy'],repetitions=12)['storage_mib'],7168)

    def test_registration_is_idempotent_and_never_relabels_an_existing_version(self):
        first=self.register()
        self.assertEqual(self.register(),first)
        with self.assertRaises(ArtifactError):
            self.registry.register(image=IMAGE_A,name='Renamed',jar=self.jar,defaults=self.defaults)
        self.assertEqual(len(self.registry.load()),1)

    def test_default_selection_and_image_check_do_not_replace_bundles(self):
        first=self.register()
        second=self.registry.register(image=IMAGE_B,name='Version B',jar=self.jar,defaults=self.defaults)
        self.registry.select(second)
        with self.assertRaisesRegex(ArtifactError,'Configured image'):
            self.registry.load(expected_image=IMAGE_A)
        self.registry.select(first)
        self.assertEqual(next(iter(self.registry.load(expected_image=IMAGE_A))),first)
        with self.assertRaises(ArtifactError): self.registry.select('../release')

    def test_legacy_upgrade_keeps_original_id_paths_and_receipt_bytes(self):
        original=self.state/'release'; original.mkdir(mode=0o700,parents=True)
        (original/'defaults').mkdir(mode=0o700)
        (original/'model.jar').write_bytes(self.jar.read_bytes())
        (original/'defaults'/'scenario_CPI.xlsx').write_bytes(b'original frozen default')
        write_json(original/'release.json',dict(image=IMAGE_A,model=fingerprint(original/'model.jar'),
                                               defaults=inventory(original/'defaults')))
        before=(original/'release.json').read_bytes()
        key='local-'+fingerprint(original/'model.jar')['sha256'][:16]
        self.jar.write_bytes(b'new checkout model')
        loaded=frozen_release(self.state,IMAGE_A)
        self.assertEqual(list(loaded),[key])
        self.assertEqual(loaded[key]['jar'],original/'model.jar')
        self.assertEqual(loaded[key]['resource_policy'],LEGACY_POLICY)
        self.assertEqual((original/'release.json').read_bytes(),before)
        newer=self.register(make_default=True)
        self.assertIn(key,self.registry.load())
        self.assertEqual(next(iter(self.registry.load())),newer)
        self.assertEqual((original/'release.json').read_bytes(),before)

    def test_restart_uses_registry_and_never_reads_current_checkout(self):
        key=self.register()
        with patch('deploy.multirun.local_web.ROOT',self.root/'missing-checkout'):
            self.assertEqual(next(iter(frozen_release(self.state,IMAGE_A))),key)

    def test_first_start_bootstraps_a_versioned_bundle(self):
        (self.root/'multirun.jar').write_bytes(self.jar.read_bytes())
        self.defaults.rename(self.root/'input')
        with patch('deploy.multirun.local_web.ROOT',self.root):
            key=next(iter(frozen_release(self.state,IMAGE_A)))
        self.assertTrue(key.startswith('release-'))
        self.assertTrue(self.registry.load()[key]['helper'].is_file())

    def test_corruption_or_added_defaults_block_startup_instead_of_using_new_version(self):
        for relative in ('model.jar','PrepareDataset.java','run.sh','defaults/scenario_CPI.xlsx','release.json'):
            with self.subTest(relative=relative):
                state=self.root/('corrupt-'+relative.replace('/','-'))
                registry=ReleaseRegistry(state)
                key=registry.register(image=IMAGE_A,name='A',jar=self.jar,defaults=self.defaults)
                path=registry.root/key/relative
                path.chmod(0o600); path.write_bytes(b'corrupted')
                with self.assertRaises((ArtifactError,ValueError)): registry.load()
        key=self.register()
        (self.registry.root/key/'defaults'/'extra.xlsx').write_bytes(b'not reviewed')
        with self.assertRaises(ArtifactError): self.registry.load()

    def test_missing_old_bundle_blocks_upgrade_and_is_not_recreated(self):
        key=self.register()
        directory=self.registry.root/key
        hidden=self.root/'saved'; directory.rename(hidden)
        with self.assertRaisesRegex(ArtifactError,'missing'): self.registry.load()
        with self.assertRaises(ArtifactError): self.register(policy=deepcopy(DEFAULT_POLICY))
        self.assertFalse(directory.exists())

    def test_symlinks_and_hardlinked_metadata_are_rejected(self):
        key=self.register()
        file=self.registry.root/key/'model.jar'; file.unlink(); file.symlink_to(self.jar)
        with self.assertRaises(ArtifactError): self.registry.load()
        linked=self.root/'linked'; linked.symlink_to(self.state,target_is_directory=True)
        with self.assertRaises(ArtifactError): ReleaseRegistry(linked).load()
        self.registry.catalogue.chmod(0o600)
        os.link(self.registry.catalogue,self.root/'metadata-alias')
        with self.assertRaises(ArtifactError): self.registry.load()

    def test_linked_lock_and_source_workbooks_are_rejected_before_mutation(self):
        self.registry.root.mkdir(mode=0o700,parents=True)
        (self.registry.root/'registry.lock').symlink_to(self.jar)
        with self.assertRaises(OSError): self.register()
        (self.registry.root/'registry.lock').unlink()
        (self.defaults/'linked.xlsx').symlink_to(self.jar)
        with self.assertRaises(ArtifactError): self.register()
        self.assertFalse(self.registry.catalogue.exists())

    def test_interrupted_catalogue_commit_adopts_only_the_same_complete_bundle(self):
        original=releases.atomic_json
        def fail_catalogue(path,value):
            if path==self.registry.catalogue: raise OSError('fictional interrupted commit')
            original(path,value)
        with patch.object(releases,'atomic_json',side_effect=fail_catalogue):
            with self.assertRaises(OSError): self.register()
        self.assertFalse(self.registry.catalogue.exists())
        key=self.register()
        self.assertEqual(list(self.registry.load()),[key])
        self.assertEqual(len(list(self.registry.root.glob('release-*'))),1)

    def test_competing_registrations_keep_both_catalogue_entries(self):
        def register(index):
            return ReleaseRegistry(self.state).register(image=(IMAGE_A if index==0 else IMAGE_B),
                name=f'Version {index}',jar=self.jar,defaults=self.defaults)
        with ThreadPoolExecutor(max_workers=2) as workers:
            keys=list(workers.map(register,range(2)))
        self.assertEqual(set(self.registry.load()),set(keys))

    def test_invalid_resource_policy_cannot_be_registered(self):
        for field,value in (('cpu_millis',True),('memory_mib',1024),('storage_mib',-1)):
            policy=deepcopy(DEFAULT_POLICY); policy['preparation'][field]=value
            with self.subTest(field=field),self.assertRaises(ArtifactError): self.register(policy=policy)
        for large in (1,4096):
            policy=deepcopy(DEFAULT_POLICY); policy['simulation']['large_memory_mib']=large
            with self.assertRaises(ArtifactError): check_policy(policy)
        self.assertFalse(self.registry.catalogue.exists())

    def test_legacy_policy_preserves_allocations_and_new_releases_scale_with_repetitions(self):
        self.assertEqual(simulation_resources(LEGACY_POLICY,population=20000,repetitions=1000),
                         dict(cpu_millis=2000,memory_mib=4096,storage_mib=10240))
        for count,total in ((1,4352),(3,4864),(12,7168),(1000,260096)):
            with self.subTest(count=count):
                self.assertEqual(simulation_resources(DEFAULT_POLICY,population=20000,repetitions=count),
                    dict(cpu_millis=2000,memory_mib=4096,storage_mib=total))
        self.assertEqual(simulation_resources(DEFAULT_POLICY,population=50000,repetitions=1)['memory_mib'],5120)
        policy=deepcopy(DEFAULT_POLICY); policy['simulation']['storage']['per_repetition_mib']=100
        self.assertEqual(simulation_resources(policy,population=20000,repetitions=3)['storage_mib'],4396)
        policy['simulation']['storage']['per_repetition_mib']=2**31-1
        with self.assertRaises(ArtifactError): simulation_resources(policy,population=20000,repetitions=2)

    def test_input_copy_floor_rounds_up_without_multiplying_inputs_per_repetition(self):
        minimum=6*1024**3+1
        first=simulation_storage(DEFAULT_POLICY,repetitions=1,minimum_setup_bytes=minimum)
        many=simulation_storage(DEFAULT_POLICY,repetitions=1000,minimum_setup_bytes=minimum)
        self.assertEqual(first['setup_mib'],6145)
        self.assertEqual(first['configured_setup_mib'],4096)
        self.assertEqual(first['storage_mib'],6401)
        self.assertEqual(many['setup_mib'],first['setup_mib'])
        self.assertEqual(many['storage_mib']-first['storage_mib'],999*256)
        self.assertEqual(DEFAULT_POLICY['simulation']['storage']['setup_mib'],4096)

    def test_invalid_counts_sizes_and_storage_policies_are_rejected(self):
        for count in (0,1001,True,3.0):
            with self.subTest(count=count),self.assertRaises(ArtifactError):
                simulation_storage(DEFAULT_POLICY,repetitions=count)
        for size in (-1,True,1.5,2**31*1024**2):
            with self.subTest(size=size),self.assertRaises(ArtifactError):
                simulation_storage(DEFAULT_POLICY,repetitions=1,minimum_setup_bytes=size)
        for setup,per in ((4095,512),(4096,-1),(4096,True)):
            policy=deepcopy(DEFAULT_POLICY)
            policy['simulation']['storage']=dict(setup_mib=setup,per_repetition_mib=per)
            with self.assertRaises(ArtifactError): check_policy(policy)
