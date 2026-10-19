"""(C) Copyright 2026, by Ross Richardson

Same-JVM repetition boundaries, partial-evidence rejection and labelled reports.
Only fictional numeric observations are used; no simulations or native services.
@author ross richardson
"""
from copy import deepcopy
import csv
from pathlib import Path
import tempfile
import unittest

from . import research_memory_reporting as memory
from . import research_year_profile as years
from .artifacts import ArtifactError
from .test_research_year_profile import profile_rows


class RepeatedMemoryReportingTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.rows=profile_rows(population=20000,end_year=2035,heap_mib=2560,memory_mib=3584)
        self.profile=years.summarise(self.rows,population=20000,seeds=['606','607'],end_year=2035,milestones=(2035,))
        self.attempt=dict(single_jvm_verified=True,observed_jvm_pid=1234,outcome='success')

    def report(self,rows=None,attempt=None):
        return memory.repeated_check(self.rows if rows is None else rows,self.profile,
            self.attempt if attempt is None else attempt)

    def test_repetitions_have_separate_peaks_and_setup_includes_the_next_build(self):
        before=deepcopy(self.rows);result=self.report();first,second=result['repetitions']
        self.assertTrue(result['single_jvm_verified']);self.assertFalse(result['forced_gc'])
        self.assertEqual([r['seed'] for r in result['repetitions']],['606','607'])
        self.assertEqual(first['setup']['elapsed_ms'],1000)
        self.assertEqual(second['setup']['elapsed_ms'],2000)
        self.assertEqual(first['including_setup']['sampled_peaks']['heap_used_bytes'],2375*1024**2)
        self.assertEqual(second['including_setup']['sampled_peaks']['heap_used_bytes'],2000*1024**2)
        self.assertEqual(second['simulation_and_output']['sampled_peaks']['heap_used_bytes'],2000*1024**2)
        transition=result['transitions'][0]
        self.assertEqual(transition['unassigned_timer_samples'],1)
        self.assertEqual(transition['lowest_sampled_heap']['heap_used_bytes'],437*1024**2)
        self.assertEqual(transition['lowest_sampled_heap']['heap_committed_bytes'],2560*1024**2)
        self.assertIn('not a forced-GC',result['interpretation'])
        self.assertEqual(self.rows,before)

    def test_jvm_identity_and_completed_lifecycle_are_required(self):
        for change in (dict(single_jvm_verified=False),dict(observed_jvm_pid=0),
                dict(observed_jvm_pid=True),dict(outcome='heap_limit')):
            with self.subTest(change=change),self.assertRaises(ArtifactError):
                self.report(attempt={**self.attempt,**change})
        with self.assertRaises(ValueError):self.report(rows=self.rows[:-1])
        with self.assertRaises(ValueError):self.report(rows=self.rows+self.rows)
        changed=deepcopy(self.profile);changed['container_limit_bytes']+=1024
        with self.assertRaises(ArtifactError):memory.repeated_check(self.rows,changed,self.attempt)

    def test_no_unassigned_timer_sample_is_unavailable_not_zero(self):
        rows=[dict(r) for r in self.rows if not(r['kind']=='sample' and r['year']==0)]
        for i,row in enumerate(rows):row['sequence']=i
        result=self.report(rows=rows)
        self.assertEqual(result['transitions'][0]['unassigned_timer_samples'],0)
        self.assertIsNone(result['transitions'][0]['lowest_sampled_heap'])
        self.assertIsNone(result['transitions'][0]['lowest_sampled_working'])

    def test_html_and_csv_label_repetitions_limits_timings_and_failure_without_a_false_peak(self):
        self.profile['repeat_check']=self.report()
        failed=dict(name='population-50000-through-2070',outcome='memory_limit',archive='<private-path>',
            settings=dict(population=50000,end_year=2070,heap_mib=3072,memory_mib=4096,cpu_millis=2000))
        memory.write_report(self.root,[self.profile],[failed])
        with (self.root/'memory-repetitions.csv').open() as source:rows=list(csv.DictReader(source))
        self.assertEqual([r['seed'] for r in rows],['606','607'])
        self.assertEqual([r['repetition'] for r in rows],['1','2'])
        self.assertEqual(rows[0]['heap_limit_gib'],'2.5');self.assertEqual(rows[0]['container_limit_gib'],'3.5')
        text=(self.root/'memory-comparison.html').read_text()
        for label in ('Maximum heap (GiB)','Container (GiB)','Including setup (min)',
                'Heap at run finish before disposal (GiB)','Committed heap at that sample (GiB)',
                'FAILED: memory_limit','cannot rule out a slower leak','&lt;private-path&gt;'):
            self.assertIn(label,text)
        self.assertNotIn('<private-path>',text)
        self.assertIn('A full memory-matrix rerun is unnecessary',text)

    def test_all_failed_candidates_still_produce_a_readable_report(self):
        failed=dict(name='candidate',outcome='heap_limit',archive='archive',
            settings=dict(population=20000,end_year=2035,heap_mib=2560,memory_mib=3584,cpu_millis=2000))
        memory.write_report(self.root,[],[failed])
        self.assertIn('FAILED: heap_limit',(self.root/'memory-comparison.html').read_text())
        with (self.root/'memory-repetitions.csv').open() as source:self.assertEqual(list(csv.DictReader(source)),[])


if __name__=='__main__':unittest.main()
