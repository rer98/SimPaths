"""(C) Copyright 2026, by Ross Richardson
Chart delivery budgets preserve scientific identities and bounded selected reads.
@author ross richardson
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from deploy._workflow import frontend_path
import sys
sys.path.insert(0,str(frontend_path()))
from jasmine_web.batch.aggregate_limits import AggregateLimits,DEFAULT_LIMITS
from jasmine_web.batch.aggregate_sections import VIEW_LIMIT,VIEW_ROWS
from jasmine_web.batch.visualiser import Visualiser
from .visualiser_backend import VisualiserBackend

LONG_LIMITS=AggregateLimits(96*1024**2,192*1024**2,200000,400000)


class PublicationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.identity=dict(build=dict(format='simpaths.visualiser.build.v1',mode='development-paired',
            files={'runner.cjs':'a'*64,'calculation.cjs':'b'*64,'visualiser.js':'c'*64}),
            memory_mib=1792,node='v22.23.1',publication='own-and-public-preview-v1')
        self.v=Visualiser.__new__(Visualiser)
        self.v.backend=SimpleNamespace(calculation_identity=VisualiserBackend.calculation_identity)
        self.v.publication_limits=LONG_LIMITS
        self.v.identity=dict(self.identity,publication_limits=LONG_LIMITS.describe())
    def test_larger_delivery_budget_keeps_existing_default_publications_compatible(self):
        self.assertTrue(self.v._same_backend(self.identity))
        self.assertEqual(self.v._calculation_identity(self.identity),self.v._calculation_identity(self.v.identity))
    def test_prior_nondefault_budget_is_compatible_when_current_budget_contains_it(self):
        earlier=dict(self.identity,publication_limits=AggregateLimits(32*1024**2,96*1024**2,80000,240000).describe())
        self.assertTrue(self.v._same_backend(earlier))
    def test_smaller_budget_does_not_authorise_a_larger_recorded_publication(self):
        self.v.publication_limits=DEFAULT_LIMITS;self.v.identity=self.identity
        self.assertFalse(self.v._same_backend(dict(self.identity,publication_limits=LONG_LIMITS.describe())))
    def test_calculation_runtime_and_scientific_identity_changes_are_still_rejected(self):
        for field in ('runner.cjs','calculation.cjs','node','memory_mib','publication'):
            changed=deepcopy(self.identity)
            if field.endswith('.cjs'):changed['build']['files'][field]='d'*64
            else:changed[field]='different'
            with self.subTest(field=field):self.assertFalse(self.v._same_backend(changed))
    def test_malformed_delivery_budgets_cannot_be_ignored(self):
        with self.assertRaises(ValueError):self.v._same_backend(dict(self.identity,publication_limits={'untrusted':1}))
    def test_selected_browser_response_limits_do_not_expand_with_retained_chart_files(self):
        self.assertEqual(VIEW_LIMIT,8*1024**2);self.assertEqual(VIEW_ROWS,20000)


if __name__=='__main__':unittest.main()
