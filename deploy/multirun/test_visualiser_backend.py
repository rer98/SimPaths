"""(C) Copyright 2026, by Ross Richardson

Synthetic native CSV proof of pinned VM calculations and publication safeguards.
No user records or provider microdata are read.
@author ross richardson
"""
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest

from deploy._workflow import frontend_path
from deploy.multirun.visualiser_backend import VisualiserBackend

BUILD=os.environ.get('SIMPATHS_VISUALISER_TEST_BUILD')


def csv_text(rows):
    output=io.StringIO()
    writer=csv.DictWriter(output,fieldnames=list(rows[0]))
    writer.writeheader();writer.writerows(rows)
    return output.getvalue()


def native_texts(value=10, *, category='White', regions=('UKI',)):
    """Fictional values, including IDs above JavaScript's exact integer limit."""
    unit_count=max(2,len(regions))
    people=[dict(run='1',time='2019.0',id_Person=str(70000000000000001+i),
        idBu=str(9007199254740993+i%unit_count),wgt=str(1+2*(i%2)),demAge='30',
        demMaleFlag='Male' if i%2==0 else 'Female',demEthnC6=category,
        healthDsblLongtermFlag='False',eduHighestC4='High',demPartnerStatus='Partnered',
        labC4='EmployedOrSelfEmployed',labHrsWorkWeek='30',yBenUCReceivedFlag='true',
        healthPsyDstrss0to12='2',healthMentalMcs=str(value),healthPhysicalPcs='50',
        healthSelfRated='Good',demLifeSatScore0to10='7',healthWbScore0to36='12',
        careNeedFlag='False',private_note='PRIVATE_ROW_SENTINEL') for i in range(12)]
    benefits=[dict(run='1',time='2019.0',id_BenefitUnit=str(9007199254740993+i),
        yDispEquivYear='24000',yBenAmountMonth='120',yHhQuintilesMonthC5='Q3',
        region=regions[i%len(regions)],yBenUCReceivedFlag='0') for i in range(unit_count)]
    return csv_text(people),csv_text(benefits)


def sources(root, *, category='White', regions=('UKI',), alternatives=1):
    result=[]
    groups=[('Baseline',0,'Baseline'),*[('Scenario',5*i,'Scenario' if alternatives==1 else 'Scenario_'+str(i))
                                      for i in range(1,alternatives+1)]]
    for role,offset,prefix in groups:
        files=[];runs=[]
        for number,value in enumerate((10,20,30),1):
            folder=f'{prefix}/run_{number}'
            runs.append(dict(folder=folder,seed=str(605+number)))
            for name,text in zip(('Person.csv','BenefitUnit.csv'),native_texts(value+offset,category=category,regions=regions)):
                relative=f'{folder}/csv/{name}'
                path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
                files.append(dict(name=relative,path=relative,bytes=path.stat().st_size,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        result.append(dict(configuration=dict(id=prefix.lower(),name=role+' example',role=role,
            dataset='own-inputs',model='sha256:'+'a'*64,runs=runs),files=files))
    return result


@unittest.skipUnless(BUILD,'Set SIMPATHS_VISUALISER_TEST_BUILD to a pinned development build')
class VisualiserBackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='simpaths-visualiser-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.work=self.root/'private';self.work.mkdir()
        old=list(sys.path);self.addCleanup(lambda:setattr(sys,'path',old))
        sys.path.insert(0,str(frontend_path()))
        self.backend=VisualiserBackend(BUILD,self.root,public_datasets=['public-training'])

    def command(self,argv,*,pass_fds=()):
        result=subprocess.run(argv,pass_fds=pass_fds,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        if result.returncode:
            raise ValueError('Private calculation failed')

    def test_real_module_aggregates_separate_roles_and_preserves_seed_mapping(self):
        from jasmine_web.batch.visualiser import validate_publication
        progress=[]
        selected=sources(self.root)
        result=self.backend.process(selected,self.work,self.command,progress.append)
        validate_publication(result)
        self.assertEqual(progress,list(range(1,7)))
        for role,expected in [('baseline',20),('scenario',25)]:
            row=next(r for r in result['rows'] if r['scenario']==role and
                r['variable']=='Mental Component Summary (MCS)' and r['stratifier']=='Overall'
                and r['variable_value'] in ('Mean','Continuous Mean'))
            self.assertAlmostEqual(row['mean_value'],expected)
            self.assertEqual(row['n_runs'],3)
        text=json.dumps(result)
        for private in ('PRIVATE_ROW_SENTINEL','id_Person','id_BenefitUnit','70000000000000001',str(self.root)):
            self.assertNotIn(private,text)
        self.assertTrue(result['comparison_available'])
        scenario=next(r for r in result['rows'] if r['scenario']=='scenario' and
            r['variable']=='Mental Component Summary (MCS)' and r['stratifier']=='Overall'
            and r['variable_value'] in ('Mean','Continuous Mean'))
        self.assertEqual(scenario['paired_n_runs'],3)
        self.assertEqual(scenario['paired_mean_delta'],5)
        self.assertEqual((scenario['paired_lower_ci'],scenario['paired_upper_ci']),(5,5))

    def test_platform_interface_stages_rows_without_parsing_them_in_the_server(self):
        from unittest.mock import patch
        from jasmine_web.batch.aggregate_io import AggregateFiles
        selected=sources(self.root)
        with patch('jasmine_web.batch.visualiser.artifact',side_effect=AssertionError('Server must not parse aggregate rows')):
            staged=self.backend.process_files(selected,self.work,self.command,lambda _:None)
        self.assertIsInstance(staged,AggregateFiles)
        self.assertEqual(staged.paths,(self.work/'aggregate.json',))
        self.assertGreater(staged.paths[0].stat().st_size,0)

    def test_multiple_alternatives_use_independent_unchanged_calculations_and_one_baseline(self):
        from jasmine_web.batch.visualiser import validate_publication
        from deploy.multirun.vm_boundary import aggregate_envelope
        self.assertTrue(self.backend.supports_comparison_sets)
        selected=sources(self.root,alternatives=2)
        progress=[]
        published=self.backend.process(selected,self.work,self.command,progress.append,comparison_set=True)
        validate_publication(published,configurations=[s['configuration'] for s in selected])
        configurations=[s['configuration'] for s in selected]
        envelope=dict(format='simpaths.visualiser.v2',backend=self.backend.identity,experiment='Fictional',
            configurations=configurations,comparison=dict(baseline=configurations[0]['id'],
                scenarios=[c['id'] for c in configurations[1:]]),data=published)
        self.assertIs(aggregate_envelope(envelope),envelope)
        self.assertEqual(progress,list(range(1,10)))
        self.assertEqual([s['configuration'] for s in published['series']],['baseline','scenario_1','scenario_2'])
        for series,expected in zip(published['series'],(20,25,30)):
            overall=next(r for r in series['rows'] if r['variable']=='Mental Component Summary (MCS)'
                and r['stratifier']=='Overall' and r['variable_value'] in ('Mean','Continuous Mean'))
            self.assertAlmostEqual(overall['mean_value'],expected)
            self.assertEqual(overall['n_runs'],3)
            self.assertEqual(overall['total_sample'],36)
            if series['configuration']!='baseline':
                self.assertEqual(overall['paired_n_runs'],3)
                self.assertEqual(overall['paired_mean_delta'],expected-20)
                self.assertEqual((overall['paired_lower_ci'],overall['paired_upper_ci']),(expected-20,expected-20))
        for private in ('PRIVATE_ROW_SENTINEL','id_Person','id_BenefitUnit',str(self.root)):
            self.assertNotIn(private,json.dumps(published))

    def test_pairing_uses_actual_seed_when_scenario_runs_are_in_a_different_order(self):
        selected=sources(self.root)
        selected[1]['configuration']['runs'].reverse()
        published=self.backend.process(selected,self.work,self.command,lambda _:None)
        overall=next(r for r in published['rows'] if r['scenario']=='scenario'
            and r['variable']=='Mental Component Summary (MCS)' and r['stratifier']=='Overall'
            and r['variable_value'] in ('Mean','Continuous Mean'))
        self.assertEqual(overall['paired_n_runs'],3)
        self.assertEqual(overall['paired_mean_delta'],5)
        self.assertEqual((overall['paired_lower_ci'],overall['paired_upper_ci']),(5,5))

    def test_paired_uncertainty_uses_run_differences_and_preserves_new_chart_metrics(self):
        selected=sources(self.root)
        for index,run in enumerate(selected[1]['configuration']['runs']):
            item=next(f for f in selected[1]['files'] if f['name']==run['folder']+'/csv/Person.csv')
            content=native_texts((index+1)*12)[0].encode()
            (self.root/item['path']).write_bytes(content)
            item.update(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())
        published=self.backend.process(selected,self.work,self.command,lambda _:None)
        overall=next(r for r in published['rows'] if r['scenario']=='scenario'
            and r['variable']=='Mental Component Summary (MCS)' and r['stratifier']=='Overall'
            and r['variable_value']=='Continuous Mean')
        # Matched differences are 2, 4, 6: sample SD=2 and SE=2/sqrt(3).
        self.assertAlmostEqual(overall['paired_mean_delta'],4)
        self.assertAlmostEqual(overall['paired_lower_ci'],4-1.96*2/math.sqrt(3))
        self.assertAlmostEqual(overall['paired_upper_ci'],4+1.96*2/math.sqrt(3))
        self.assertEqual(overall['paired_n_runs'],3)
        self.assertTrue({'wage_bin','income_bin','pyramid_bin'}<=
            {r['metric_type'] for r in published['rows']})
        # Existing upstream suppression still hides pooled samples below 20.
        self.assertTrue(all(r['mean_value'] is None for r in published['rows'] if r['total_sample']<20))

    def test_older_verified_application_keeps_pair_support_without_offering_sets(self):
        legacy=self.root/'legacy-build'
        shutil.copytree(BUILD,legacy)
        application=b'/* Fictional v1-only application asset. */'
        (legacy/'visualiser.js').write_bytes(application)
        manifest=json.loads((legacy/'build.json').read_text())
        manifest['files']['visualiser.js']=hashlib.sha256(application).hexdigest()
        manifest['mode']='development-levels'
        (legacy/'build.json').write_text(json.dumps(manifest))
        backend=VisualiserBackend(legacy,self.root)
        self.assertFalse(backend.supports_comparison_sets)
        result=backend.process(sources(self.root),self.work,self.command,lambda _:None)
        self.assertEqual({r['scenario'] for r in result['rows']},{'baseline','scenario'})
        self.assertFalse(result['comparison_available'])
        with self.assertRaises(ValueError):
            backend.process([],self.work,self.command,lambda _:None,comparison_set=True)

    def test_unexpected_raw_category_is_not_published(self):
        with self.assertRaises(ValueError):
            self.backend.process(sources(self.root,category='PRIVATE_CATEGORY_SENTINEL'),self.work,self.command,lambda _:None)

    def test_all_native_regions_use_chart_labels_without_changing_statistics(self):
        from jasmine_web.batch.visualiser import validate_publication
        regions=('UKC','UKD','UKE','UKF','UKG','UKH','UKI','UKJ','UKK','UKL','UKM','UKN')
        expected={'North East','North West','Yorkshire and the Humber','East Midlands',
            'West Midlands','East of England','London','South East','South West',
            'Wales','Scotland','Northern Ireland'}
        published=self.backend.process(sources(self.root,regions=regions),self.work,self.command,lambda _:None)
        validate_publication(published)
        observed={r['stratifier_value'] for r in published['rows'] if r['stratifier']=='Region'}
        self.assertEqual(observed,expected)
        for role,mean in [('baseline',20),('scenario',25)]:
            overall=next(r for r in published['rows'] if r['scenario']==role and
                r['variable']=='Mental Component Summary (MCS)' and r['stratifier']=='Overall'
                and r['variable_value'] in ('Mean','Continuous Mean'))
            self.assertAlmostEqual(overall['mean_value'],mean)
            self.assertEqual(overall['n_runs'],3)
            self.assertEqual(overall['total_sample'],36)
        # Unknown region text must still be blocked rather than becoming a
        # browser-visible label. The aliases are a fixed vocabulary, not a filter.
        with self.assertRaises(ValueError):
            self.backend.process(sources(self.root,regions=('PRIVATE_REGION_SENTINEL',)),
                self.work,self.command,lambda _:None)

    def test_changed_csv_and_unrecognised_native_schema_are_rejected(self):
        selected=sources(self.root)
        first=selected[0]['files'][0]
        (self.root/first['path']).write_text('unrelated,columns\n1,2\n')
        from jasmine_web.batch.results import OutputUnavailable
        with self.assertRaises(OutputUnavailable):
            self.backend.process(selected,self.work,self.command,lambda _:None)
        content=(self.root/first['path']).read_bytes()
        first.update(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())
        with self.assertRaises(ValueError):
            self.backend.process(selected,self.work,self.command,lambda _:None)

    def test_only_owner_inputs_and_explicitly_public_datasets_are_enabled(self):
        for origins,expected in [([['user','owner']],True),([['user','other']],False),
            ([['provider','public-training']],True),([['provider','restricted']],False),
            ([['user','owner'],['provider','restricted']],False),([],False)]:
            self.assertEqual(self.backend.allowed(origins,'owner'),expected)
        self.assertNotIn('runner.cjs',self.backend.assets)
        self.assertNotIn('calculation.cjs',self.backend.assets)
        self.assertFalse(any(name.endswith('.map') for name in self.backend.assets))

    def test_public_application_assets_exclude_bundled_data_and_private_calculations(self):
        expected={'pmh_logo.png','UKRILogo.png','Interpreting-results.html',
                  'Interpreting-results.css','citation.html','citation.css'}
        self.assertTrue(expected<=self.backend.assets.keys())
        self.assertIn('App.js',self.backend.manifest['source_hashes'])
        self.assertTrue(self.backend.assets['pmh_logo.png'].startswith(b'\x89PNG\r\n\x1a\n'))
        for name in ('Interpreting-results.html','citation.html'):
            text=self.backend.assets[name].decode()
            for blocked in ('<script','fonts.googleapis.com','fonts.gstatic.com','onerror='):
                self.assertNotIn(blocked,text)
            self.assertIn('/visualiser-assets/pmh_logo.png',text)
            self.assertIn('Return to SimPaths Online',text)
        self.assertNotIn('SimPaths_All_Aggregated_Outputs.csv',self.backend.assets)
        self.assertNotIn('build.json',self.backend.assets)


if __name__=='__main__':
    unittest.main()
