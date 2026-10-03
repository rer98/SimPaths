"""(C) Copyright 2026, by Ross Richardson

Check storage-calibration sampling and seed attribution with fictional file trees.
@author ross richardson
"""
from pathlib import Path
import sys
import tempfile
import unittest

from deploy._workflow import frontend_path
from .configuration import normalise
from .storage_proof import configuration, run_summary, sample


class StorageProofTests(unittest.TestCase):
    def setUp(self):
        old=list(sys.path); self.addCleanup(lambda:setattr(sys,'path',old))
        sys.path.insert(0,str(frontend_path()))
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)

    def test_full_length_configuration_has_one_card_and_requested_seed_count(self):
        for count in (1,3):
            data=normalise(configuration(dict(revision='quickstart-fictional'),count)).as_dict()
            self.assertEqual(data['common'],dict(country='UK',start_year=2019,end_year=2026,population=50000))
            self.assertEqual(len(data['run_sets']),1)
            self.assertEqual(data['seed_plan']['seeds'],[str(606+i) for i in range(count)])

    def test_size_summary_uses_seed_metadata_without_copying_csv_contents(self):
        for directory,seed in (('later-timestamp',606),('earlier-timestamp',607)):
            run=self.path/'work/output'/directory
            (run/'input').mkdir(parents=True); (run/'csv').mkdir()
            (run/'input/options.txt').write_text('randomSeedIfFixed: '+str(seed)+'\n')
            (run/'csv/Person.csv').write_bytes(b'fictional'*1024)
            if seed==606: (run/'input/parameters.xlsx').write_bytes(b'parameters')
        (self.path/'work/input').mkdir(); (self.path/'work/input/input.mv.db').write_bytes(b'input database')
        measured=sample(self.path)
        self.assertGreater(measured['work_bytes'],measured['input_bytes'])
        self.assertGreater(measured['csv_bytes'],0)
        runs=run_summary(self.path)
        self.assertEqual([r['seed'] for r in runs],['606','607'])
        self.assertEqual([r['seed'] for r in runs if r['copied_input_bytes']],['606'])
        self.assertNotIn('fictional',str(runs))

    def test_sampler_rejects_linked_files_instead_of_reading_external_paths(self):
        (self.path/'work').mkdir()
        external=self.path/'outside'; external.write_bytes(b'external private file')
        (self.path/'work/link').symlink_to(external)
        with self.assertRaises(ValueError): sample(self.path)
