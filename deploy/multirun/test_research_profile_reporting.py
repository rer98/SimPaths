"""(C) Copyright 2026, by Ross Richardson

Workspace burst, phase reporting and output-label regressions with fictional data.
No scientific simulation, Docker, database or network access is needed.
@author ross richardson
"""
from copy import deepcopy
import csv
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from deploy.multirun import research_profile_reporting as reporting
from deploy.multirun import research_calibration as calibration
from deploy.multirun import research_year_profile as years
from deploy.multirun.test_research_year_profile import profile_rows

MIB=1024**2


def measurement(work):
    return dict(work_bytes=work,input_bytes=0,csv_bytes=work,snapshot_bytes=0,other_bytes=0,
        request_bytes=10,work_and_request_bytes=work+10)


def fixture_workspace():
    rows=[]
    for elapsed,size in ((0,100),(10000,300),(20000,250),(30000,400)):
        reporting.append_workspace(rows,measurement(size),elapsed)
    return rows


def fixture_profile(end_year=2021):
    rows=profile_rows(end_year=end_year,repetitions=1)
    milestones=sorted({min(end_year,2020),end_year})
    profile=years.summarise(rows,population=1000,seeds=['606'],end_year=end_year,milestones=milestones)
    profile['workspace']=reporting.workspace_summary(fixture_workspace())
    for rep in profile['repetitions']:
        for i,row in enumerate(rep['annual']):
            row.update(exported_person_rows=1000,annual_csv_bytes=100*MIB,cumulative_csv_bytes=(i+1)*100*MIB)
    return profile


class ResourceReportingTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)

    def test_workspace_growth_distinguishes_net_deletion_bursts_and_peak(self):
        result=reporting.workspace_summary(fixture_workspace())
        self.assertEqual(result['samples'],4)
        self.assertEqual(result['sampled_peak']['work_and_request_bytes'],410)
        largest=result['largest_observed_growth']
        self.assertEqual((largest['growth_bytes'],largest['elapsed_ms'],largest['first_sequence'],largest['last_sequence']),
            (200,10000,0,1))
        self.assertEqual(largest['bytes_per_second'],20)
        self.assertEqual(result['maximum_sample_gap_ms'],10000)
        self.assertIn('not an atomic',result['limitation'])
        one=reporting.workspace_summary(fixture_workspace()[:1])
        self.assertIsNone(one['largest_observed_growth']);self.assertIsNone(one['maximum_sample_gap_ms'])

    def test_workspace_roundtrip_keeps_unavailable_context_and_rejects_overwrites_and_links(self):
        rows=fixture_workspace();path=self.root/'workspace.jsonl'
        reporting.write_workspace(path,rows)
        self.assertEqual(reporting.read_workspace(path),rows)
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertEqual(rows[0]['year_sample_sequence'],-1)
        with self.assertRaises(FileExistsError):reporting.write_workspace(path,rows)
        link=self.root/'linked';link.symlink_to(path)
        with self.assertRaises(ValueError):reporting.read_workspace(link)
        with patch.object(reporting,'MAX_SAMPLES',1),self.assertRaises(ValueError):reporting.read_workspace(path)

    def test_workspace_malformed_private_nonnumeric_and_reversed_values_are_rejected(self):
        for key,value in (('owner','private'),('elapsed_ms',True),('csv_bytes',-1),('year',2019),
                ('work_and_request_bytes',999),('model_elapsed_ms',100)):
            row=dict(fixture_workspace()[0]);row[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):reporting.workspace_summary([row])
        rows=fixture_workspace();rows[2]['elapsed_ms']=1
        with self.assertRaises(ValueError):reporting.workspace_summary(rows)
        path=self.root/'bad.jsonl'
        for body in ('x'*4097,'{"sequence":0,"sequence":1}\n','{"sequence":NaN}\n',''):
            path.write_text(body)
            with self.subTest(body=body[:20]),self.assertRaises(ValueError):reporting.read_workspace(path)

    def test_observer_reuses_existing_metadata_sample_and_records_only_profile_mode(self):
        executor=Mock();executor.workspace.return_value=self.root;executor.docker.inspect.return_value=None
        entry=dict(container_id=None);record=dict(peak_workspace_bytes=0,year_profile_interval_ms=2000)
        with patch.object(calibration.storage_proof,'sample',return_value=measurement(100)) as walk,\
                patch.object(calibration.time,'monotonic',side_effect=[10,20]):
            calibration.observe(entry,Mock(),executor,record)
            entry['year_rows']=profile_rows(repetitions=1)[:2]
            calibration.observe(entry,Mock(),executor,record)
        self.assertEqual(walk.call_count,2);executor.docker.call.assert_not_called()
        self.assertEqual([r['elapsed_ms'] for r in entry['workspace_rows']],[0,10000])
        self.assertEqual(entry['workspace_rows'][1]['year'],2019)
        self.assertEqual(entry['workspace_rows'][1]['year_sample_sequence'],1)
        ordinary=dict(container_id=None)
        with patch.object(calibration.storage_proof,'sample',return_value=measurement(100)):
            calibration.observe(ordinary,Mock(),executor,dict(peak_workspace_bytes=0))
        self.assertNotIn('workspace_rows',ordinary)

    def test_labelled_reports_keep_units_nulls_years_horizons_and_finalisation(self):
        short=fixture_profile(2020);long=fixture_profile(2021)
        short['repetitions'][0]['annual'][0]['utilisation']['average_vcpus']=None
        reporting.write_reports(self.root,[short,long])
        html=(self.root/'comparison.html').read_text();HTMLParser().feed(html)
        for text in ('Ending earlier versus passing through','Finalisation','CPU allowance used (%)',
                'Quota periods throttled (%)','This year’s CSV (GiB)','initial; ends 2020','initial; ends 2021',
                'Unavailable','not the minimum safe heap','not a percentage of application time paused'):
            self.assertIn(text,html)
        self.assertIn('maximum heap: 4.00 GiB; container: 5.00 GiB',html)
        self.assertNotIn('src="http',html);self.assertNotIn('<script',html)
        with (self.root/'population-annual.csv').open() as source:rows=list(csv.DictReader(source))
        self.assertEqual(len(rows),5)
        self.assertEqual(rows[0]['average_vcpus'],'')
        self.assertEqual({r['simulation_end_year'] for r in rows},{'2020','2021'})
        self.assertAlmostEqual(float(rows[-1]['cumulative_csv_gib']),300/1024)

    def test_cpu_unavailable_is_kept_as_unavailable_in_phase_and_annual_reports(self):
        rows=profile_rows(repetitions=1)
        for row in rows:
            for key in years.OPTIONAL:row[key]=-1
        result=years.summarise(rows,population=1000,seeds=['606'],end_year=2021,milestones=(2020,2021))
        self.assertIsNone(result['phases']['simulation']['utilisation']['average_vcpus'])
        self.assertIsNone(result['repetitions'][0]['annual'][0]['utilisation']['cpu_allowance_percent'])


if __name__=='__main__':unittest.main()
