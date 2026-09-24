"""(C) Copyright 2026, by Ross Richardson

Test recommended deployment profiles and rejection of misleading heap evidence.

@author ross richardson
"""
import importlib.util
from pathlib import Path
import unittest


import runpy
load_tool = runpy.run_path(str(Path(__file__).resolve().parents[1] / '_tool_loader.py'))['load_tool']


def load(name):
    folder = 'acceptance' if name.startswith('run_') else 'web-quickstart'
    return load_tool(folder + '/' + name + '.py')


class ReleaseTests(unittest.TestCase):
    def test_profile_hash_ignores_permissions_but_detects_content_changes(self):
        import io
        import tarfile
        from types import SimpleNamespace
        release = load('build_release')

        def container(contents, mode):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w') as archive:
                entry = tarfile.TarInfo('profile.json')
                entry.size = len(contents)
                entry.mode = mode
                archive.addfile(entry, io.BytesIO(contents))
            return SimpleNamespace(get_archive=lambda path: ([buffer.getvalue()], {}))

        original = release.archive_hash(container(b'{"population":20000}', 0o644), '/app/profile.json')
        protected = release.archive_hash(container(b'{"population":20000}', 0o444), '/app/profile.json')
        changed = release.archive_hash(container(b'{"population":50000}', 0o444), '/app/profile.json')
        self.assertEqual(original, protected)
        self.assertNotEqual(original, changed)

    def test_profile_resources_and_actual_jvm_diagnostic(self):
        release, browser = load('build_release'), load('run_browser_acceptance')
        for pop, heap, memory in [(20000,2,4),(50000,3,5)]:
            profile = release.profile_record(pop, 'example:tag')
            self.assertEqual(heap, profile['deployment']['java_heap_gib'])
            self.assertEqual(f'{memory}Gi', profile['deployment']['memory'])
            attrs = {'HostConfig': {'Memory':memory*1024**3,'NanoCpus':2_000_000_000},
                     'Config': {'Env': [f'JAVA_OPTS=-Xmx{heap}g',
                        f'JASMINE_STORAGE_WARN_BYTES={3*1024**3}', f'JASMINE_STORAGE_RESERVE_BYTES={2*1024**3}']}}
            storage = {'enabled':True,'cleanupEnabled':True,'allowanceBytes':10*1024**3}
            result = browser.verify_deployment_settings(pop, attrs, f'Max. Heap Size: {heap}.00G', storage)
            self.assertEqual(heap*1024**3, result['heap_bytes'])
            for bad_log in ['', 'JAVA_OPTS=-Xmx2g', 'Max. Heap Size (Estimated): 1.50G']:
                with self.assertRaises(AssertionError):
                    browser.verify_deployment_settings(pop, attrs, bad_log, storage)
            with self.assertRaises(AssertionError):
                browser.verify_deployment_settings(pop, attrs, f'Max. Heap Size: {heap}.00G', {'enabled':False})

    def test_query_account_migration_records_both_database_versions(self):
        release = load('build_release')
        before = {'input/input.mv.db': 'source-db', 'input/a.xlsx': 'workbook'}
        after = {'input/input.mv.db': 'secured-db', 'input/a.xlsx': 'workbook'}
        receipt = {'format_version': 1, 'principal': 'JASMINE_WEB_READER',
                   'source_database_sha256': 'source-db', 'database_sha256': 'secured-db'}
        release.verify_query_migration(before, after, receipt)
        for changed in [dict(after, **{'input/a.xlsx': 'changed'}),
                        dict(after, **{'input/extra.xlsx': 'new'}),
                        dict(after, **{'input/input.mv.db': 'different-db'})]:
            with self.assertRaises(ValueError):
                release.verify_query_migration(before, changed, receipt)
        with self.assertRaises(ValueError):
            release.verify_query_migration(before, after, dict(receipt, source_database_sha256='wrong-source'))

    def test_archive_hashes_stream_arbitrary_chunk_boundaries(self):
        import hashlib
        import io
        import tarfile
        from types import SimpleNamespace
        release = load('build_release')
        buffer = io.BytesIO()
        contents = {'input/input.mv.db': b'x' * 100001, 'input/a.xlsx': b'workbook'}
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            for name, data in contents.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        data = buffer.getvalue()
        container = SimpleNamespace(get_archive=lambda path: (
            (data[i:i+317] for i in range(0, len(data), 317)), {}))
        self.assertEqual({name: hashlib.sha256(value).hexdigest() for name, value in contents.items()},
                         release.archive_content_hashes(container, '/app/input'))
