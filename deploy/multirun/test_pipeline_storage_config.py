"""(C) Copyright 2026, by Ross Richardson

Explicit opt-in for physical pipeline promises and pressure-only summary fallback.
Legacy healthy configuration, resource budgets and worker counts are preserved.
@author ross richardson
"""
import unittest
from . import test_vm_config as fixtures


class PipelineConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.VMConfigTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.text=(fixtures.TEMPLATE.parent/'throughput.toml.example').read_text().replace(
            'sha256:REPLACE_WITH_64_HEXADECIMAL_DIGITS','sha256:'+'a'*64)

    def enabled(self):
        return self.text.replace('[visualiser]','[visualiser]\npipeline_storage = true\ncompact_summaries = true',1)

    def test_omitted_flags_preserve_existing_parallel_resources_and_scientific_limits(self):
        ordinary=self.f.config(self.text);candidate=self.f.config(self.enabled())
        self.assertFalse(ordinary.visualiser_pipeline_storage);self.assertFalse(ordinary.visualiser_compact_summaries)
        self.assertTrue(candidate.visualiser_pipeline_storage);self.assertTrue(candidate.visualiser_compact_summaries)
        for key in ('capacity','holdback','per_user_active','runtime_setup_minutes','runtime_per_repetition_minutes',
                    'visualiser_max_workers','visualiser_seed_workers','visualiser_memory_mib','visualiser_storage_mib'):
            self.assertEqual(getattr(ordinary,key),getattr(candidate,key),key)

    def test_fallback_without_coordinated_storage_is_refused(self):
        self.assertRaises(ValueError,self.f.config,self.enabled().replace('pipeline_storage = true','pipeline_storage = false'))

    def test_promises_require_dedicated_quotas_protected_summaries_and_hard_limits(self):
        for old,new in (('dedicated_storage = true','dedicated_storage = false'),
                        ('hard_limits = true','hard_limits = false'),('summaries = true','summaries = false'),
                        ('storage_pressure = true','storage_pressure = false'),
                        ('retain_completed_summaries = true','retain_completed_summaries = false')):
            with self.subTest(setting=old),self.assertRaises(ValueError):self.f.config(self.enabled().replace(old,new))
