"""(C) Copyright 2026, by Ross Richardson

External chart-section privacy validation and legacy compatibility checks.
@author ross richardson
"""
from copy import deepcopy
import json
import unittest
from urllib.parse import parse_qs,urlsplit

from .vm_boundary import section_envelope, privacy_checks


def catalogue():
    return dict(format='simpaths.visualiser.catalogue.v1',publication='a'*64,
        configurations=[dict(id='base',name='Reference',role='Baseline',key='baseline'),
            dict(id='alt',name='Alternative',role='Scenario',key='scenario_1')],seeds=['606'],
        notice='Fictional',comparison_available=True,limits=dict(response_bytes=8*1024**2,response_rows=20000),
        variables=[dict(name='Mental Component Summary (MCS)',module='Health',years=[2019,2070],
            views=[dict(stratifier='Overall',kind='levels',bytes=1000,rows=2)])])


def view():
    return dict(format='simpaths.visualiser.view.v1',publication='a'*64,
        selection=dict(variable='Mental Component Summary (MCS)',stratifier='Overall',kind='levels',configurations=['base']),
        series=[dict(configuration='base',rows=[dict(year=2019,scenario='baseline',module='Health',
            variable='Mental Component Summary (MCS)',variable_value='Mean',stratifier='Overall',
            stratifier_value='Overall',metric_type='mean',n_runs=1,total_sample=100,min_sample=100,
            mean_sample=100,mean_value=40,sd_value=0,lower_ci=40,upper_ci=40,
            paired_mean_delta=None,paired_lower_ci=None,paired_upper_ci=None,paired_n_runs=0)])])


class SectionBoundaryTests(unittest.TestCase):
    def test_closed_catalogue_and_selected_rows_preserve_allowed_statistics(self):
        cat=catalogue();data=view()
        self.assertIs(section_envelope(cat),cat)
        self.assertIs(section_envelope(data,catalogue=cat),data)

    def test_metadata_raw_fields_private_payloads_and_invalid_budgets_are_rejected(self):
        for change in (dict(raw_records=[]),dict(seeds=['PRIVATE_PATH']),
            dict(limits=dict(response_bytes=1024**3,response_rows=20000))):
            with self.subTest(change=change),self.assertRaises(ValueError):section_envelope({**catalogue(),**change})
        for field in ('id_Person','private_path','raw_csv'):
            data=view();data['series'][0]['rows'][0][field]='PRIVATE'
            with self.assertRaises(ValueError):section_envelope(data,catalogue=catalogue())

    def test_foreign_configurations_changed_publication_and_chart_scope_are_denied(self):
        cases=[]
        data=view();data['publication']='b'*64;cases.append(data)
        data=view();data['series'][0]['configuration']='other-owner';cases.append(data)
        data=view();data['selection']['configurations']=['other-owner'];cases.append(data)
        data=view();data['series'][0]['rows'][0]['variable']='different';cases.append(data)
        data=view();data['series'][0]['rows'][0]['scenario']='scenario';cases.append(data)
        for data in cases:
            with self.subTest(data=data),self.assertRaises(ValueError):section_envelope(data,catalogue=catalogue())

    def test_external_probe_checks_both_routes_and_owner_denials_without_full_download(self):
        calls=[]
        def get(path,*,cookie='',**options):
            calls.append((path,cookie))
            if path=='/api/session' and cookie:return 200,{'cache-control':'no-store'},b'{"signed_in":true}'
            if cookie=='owner' and path.endswith('/catalogue'):
                return 200,{'cache-control':'no-store'},json.dumps(catalogue()).encode()
            if cookie=='owner' and '/view?' in path:
                return 200,{'cache-control':'no-store'},json.dumps(view()).encode()
            return 403,{},b'{}'
        checks=privacy_checks(get,'canary.txt',b'PRIVATE',cookie='owner',other_cookie='other',visualiser_key='a'*64)
        self.assertFalse(any(path.endswith('/data') for path,_ in calls))
        self.assertTrue(any('/view?' in path and who=='other' for path,who in calls))
        self.assertTrue(any(path.endswith('/catalogue') and who=='' for path,who in calls))
        self.assertTrue(any(c['status']==200 and '/view?' in c['path'] for c in checks))


if __name__=='__main__':unittest.main()
