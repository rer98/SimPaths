"""(C) Copyright 2026, by Ross Richardson

Private administrator storage measurements, bounded observation and error privacy.
Uses tiny fictional files only, without PostgreSQL, Docker or model executions.
@author ross richardson
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from deploy._workflow import frontend_path
from . import operator_status as storage


class AdministratorStorageTests(unittest.TestCase):
    def setUp(self):
        self.paths = list(sys.path)
        sys.path.insert(0, str(frontend_path()))
        self.addCleanup(lambda: setattr(sys, 'path', self.paths))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root/'state'
        self.execution = self.state/'execution'
        self.uploads = self.state/'uploads'
        self.prepared = self.state/'artifacts'
        self.visualiser = self.execution/'visualiser-cache'
        self.summaries = self.visualiser/'summaries'
        self.downloads = self.execution/'download-cache'
        self.backups = self.root/'backups'
        for path in (self.execution/'batch-fictional/work', self.uploads, self.prepared,
                     self.summaries, self.downloads, self.backups):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.files = {}
        for label, parent, size in (('job', self.execution/'batch-fictional/work', 4096),
                                   ('upload', self.uploads, 4096),
                                   ('prepared', self.prepared, 4096),
                                   ('publication', self.visualiser, 8192),
                                   ('summary', self.summaries, 4096),
                                   ('download', self.downloads, 12288),
                                   ('backup', self.backups, 16384)):
            path = parent/(label+'.fictional')
            path.write_bytes(b'x'*size)
            path.chmod(0o600)
            self.files[label] = path
        self.executor = SimpleNamespace(root=self.execution,
            workspace_quotas=SimpleNamespace(ready=lambda: dict(
                remaining_reserved_bytes=123456, reserve_bytes=654321)))
        self.service = SimpleNamespace(datasets=SimpleNamespace(root=self.uploads),
            results=SimpleNamespace(downloads=SimpleNamespace(root=self.downloads, capacity=1024**3)),
            visualiser=SimpleNamespace(root=self.visualiser,
                backend=SimpleNamespace(summary_cache=SimpleNamespace(root=self.summaries)),
                disk_storage=SimpleNamespace(physical=SimpleNamespace(reserve=321), _reserve=lambda: 123)))

    def reader(self, **kwargs):
        reader = storage.AdministratorStorage(self.service, self.state, self.executor,
                                             backup_config=None, **kwargs)
        self.addCleanup(lambda: reader._thread.join(timeout=6) if reader._thread else None)
        return reader

    def settled(self, reader):
        reader()
        reader._thread.join(timeout=6)
        self.assertFalse(reader._thread.is_alive())
        return reader()

    @staticmethod
    def areas(result):
        return {row['id']: row for row in result['areas']}

    def test_allocated_totals_exclude_caches_from_jobs_and_include_summary_subset(self):
        reader = self.reader()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.files.values()}
        result = self.settled(reader)
        areas = self.areas(result)
        allocated = lambda name: self.files[name].stat().st_blocks*512
        self.assertEqual(areas['job_workspaces']['bytes'], allocated('job'))
        self.assertEqual(areas['visualiser']['bytes'], allocated('publication')+allocated('summary'))
        self.assertEqual(areas['visualiser_summaries']['bytes'], allocated('summary'))
        self.assertEqual(areas['downloads']['bytes'], allocated('download'))
        self.assertTrue(areas['job_workspaces']['complete'])
        self.assertEqual(result['measure_state'], 'ready')
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.files.values()})
        body = json.dumps(result)
        for private in (str(self.root), '.fictional', 'batch-fictional'):
            self.assertNotIn(private, body)

    def test_constructor_does_not_scan_or_start_a_thread(self):
        with patch.object(storage.AdministratorStorage, '_scan_tree', side_effect=AssertionError('not yet')):
            reader = self.reader()
            self.assertIsNone(reader._thread)
            self.assertIsNone(reader._sample)

    def test_first_read_is_pending_and_concurrent_reads_share_one_background_scan(self):
        reader = self.reader()
        entered, release = threading.Event(), threading.Event()
        original = reader._scan_tree
        def delayed(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(timeout=3))
            return original(*args, **kwargs)
        with patch.object(reader, '_scan_tree', side_effect=delayed):
            self.assertEqual(reader()['measure_state'], 'pending')
            self.assertTrue(entered.wait(timeout=2))
            thread = reader._thread
            values = []
            callers = [threading.Thread(target=lambda: values.append(reader())) for _ in range(12)]
            for caller in callers:
                caller.start()
            for caller in callers:
                caller.join(timeout=2)
                self.assertFalse(caller.is_alive())
            self.assertEqual(len(values), 12)
            self.assertTrue(all(value['measure_state'] == 'pending' for value in values))
            self.assertIs(reader._thread, thread)
            release.set()
            thread.join(timeout=3)
        self.assertEqual(reader()['measure_state'], 'ready')

    def test_cached_category_totals_are_reused_while_filesystem_readings_are_fresh(self):
        reader = self.reader()
        result = self.settled(reader)
        previous = self.areas(result)['job_workspaces']
        self.files['job'].write_bytes(b'changed after the cached observation')
        with patch.object(reader, '_scan_tree', side_effect=AssertionError('cached')), \
             patch.object(storage, '_filesystem', return_value=(77, dict(available=True,
                 total_bytes=9000, used_bytes=8000, available_bytes=1000))) as measure:
            changed = reader()
        self.assertEqual(self.areas(changed)['job_workspaces'], previous)
        self.assertEqual(changed['filesystems'][0]['available_bytes'], 1000)
        self.assertGreater(measure.call_count, 1)

    def test_expired_cache_starts_one_new_observation(self):
        reader = self.reader(cache_seconds=1)
        self.settled(reader)
        original = reader._thread
        with reader._lock:
            reader._sampled -= 2
        result = reader()
        self.assertEqual(result['measure_state'], 'pending')
        self.assertIsNot(reader._thread, original)
        reader._thread.join(timeout=3)

    def test_shared_devices_are_one_filesystem_with_multiple_area_memberships(self):
        reader = self.reader()
        with patch.object(storage, '_filesystem', return_value=(44, dict(available=True,
                total_bytes=1000, used_bytes=700, available_bytes=300))):
            result = reader()
        self.assertEqual(len(result['filesystems']), 1)
        self.assertIn('system', result['filesystems'][0]['areas'])
        self.assertIn('job_workspaces', result['filesystems'][0]['areas'])
        self.assertIn('visualiser_summaries', result['filesystems'][0]['areas'])
        self.assertTrue(all(row['filesystem'] in (None, 'fs-1') for row in result['areas']))

    def test_filesystem_failure_is_unknown_and_does_not_hide_other_filesystems(self):
        reader = self.reader()
        original = storage._filesystem
        def measured(path):
            if path == self.uploads:
                raise OSError('password secret@example.org /restricted/file.csv')
            return original(path)
        with patch.object(storage, '_filesystem', side_effect=measured):
            result = reader()
        unavailable = [row for row in result['filesystems'] if not row['available']]
        self.assertEqual(len(unavailable), 1)
        self.assertEqual(unavailable[0]['areas'], ['uploads'])
        self.assertIsNone(unavailable[0]['used_bytes'])
        self.assertIsNone(self.areas(result)['uploads']['filesystem'])
        self.assertTrue(any(row['available'] for row in result['filesystems']))
        self.assertNotIn('secret', json.dumps(result))

    def test_symlink_in_tree_is_not_followed_and_partial_total_is_not_reported(self):
        external = self.root/'outside'; external.mkdir()
        (external/'must-not-read.csv').write_bytes(b'private raw records')
        (self.execution/'batch-fictional'/'escape').symlink_to(external, target_is_directory=True)
        result = self.settled(self.reader())
        area = self.areas(result)['job_workspaces']
        self.assertIsNone(area['bytes'])
        self.assertFalse(area['complete'])
        self.assertEqual(result['measure_state'], 'incomplete')
        self.assertTrue(self.areas(result)['visualiser']['complete'])

    def test_symlink_root_or_parent_does_not_measure_the_target_filesystem(self):
        alias = self.root/'alias'; alias.symlink_to(self.root/'state', target_is_directory=True)
        self.service.datasets.root = alias/'uploads'
        result = self.settled(self.reader())
        area = self.areas(result)['uploads']
        self.assertIsNone(area['bytes'])
        self.assertIsNone(area['filesystem'])
        self.assertFalse(area['complete'])

    def test_entry_budget_returns_unknown_instead_of_a_partial_or_zero_total(self):
        reader = self.reader(max_entries=1)
        result = self.settled(reader)
        self.assertEqual(result['measure_state'], 'incomplete')
        for area in result['areas']:
            if not area['complete']:
                self.assertIsNone(area['bytes'])

    def test_time_budget_returns_unknown_instead_of_a_partial_total(self):
        reader = self.reader(scan_seconds=0.000001)
        result = self.settled(reader)
        self.assertEqual(result['measure_state'], 'incomplete')
        self.assertIsNone(self.areas(result)['job_workspaces']['bytes'])

    def test_large_category_cannot_use_the_time_and_entries_reserved_for_other_areas(self):
        reader = self.reader(max_entries=120)
        clock = [1000.0]
        visited = []
        def sample(root, excluded, deadline, count, *, entry_limit):
            visited.append(root)
            if root == self.visualiser:
                # Consume this category's entire share, without constructing a
                # large tree or spending real wall time in a regression test.
                clock[0] = deadline
                count[0] = entry_limit
                raise TimeoutError('fictional oversized category')
            count[0] += 1
            return 4096
        with patch.object(storage.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(reader, '_scan_tree', side_effect=sample):
            result = self.settled(reader)
        areas = self.areas(result)
        self.assertFalse(areas['visualiser']['complete'])
        for area in ('downloads', 'visualiser_summaries', 'uploads', 'prepared_inputs', 'job_workspaces'):
            self.assertTrue(areas[area]['complete'], area)
        self.assertEqual(visited[-1], self.execution)
        self.assertLessEqual(clock[0], 1005.0)

    def test_model_results_roundtrip_through_the_frontend_storage_validator(self):
        from jasmine_web.batch.administration import validate_storage_report
        reader = self.reader()
        pending = reader()
        self.assertEqual(validate_storage_report(pending), pending)
        reader._thread.join(timeout=3)
        ready = reader()
        self.assertEqual(validate_storage_report(ready), ready)

    def test_commitments_are_broker_promises_and_do_not_call_physical_pool_reader(self):
        def forbidden():
            raise AssertionError('must not open a queue transaction')
        self.service.visualiser.disk_storage.physical = SimpleNamespace(reserve=321, __call__=forbidden)
        result = self.reader()()['commitments']
        self.assertTrue(result['available'])
        self.assertEqual(result['workspace_remaining_bytes'], 123456)
        self.assertEqual(result['workspace_safety_bytes'], 654321)
        self.assertEqual(result['visualiser_safety_bytes'], 321)
        self.assertEqual(result['visualiser_helper_bytes'], 123)

    def test_failed_broker_and_invalid_counters_are_unknown_not_zero(self):
        self.executor.workspace_quotas.ready = lambda: dict(remaining_reserved_bytes=-1, reserve_bytes=9)
        result = self.reader()()['commitments']
        self.assertFalse(result['available'])
        self.assertIsNone(result['workspace_remaining_bytes'])
        self.assertIsNone(result['workspace_safety_bytes'])

    def test_visualiser_allowances_use_the_read_only_budget_and_summary_is_included(self):
        from jasmine_web.batch.visualiser_storage import FORMAT
        budget = dict(format=FORMAT, initial_bytes=16*1024**3, capacity_bytes=16*1024**3,
            maximum_bytes=16*1024**3, summary_bytes=8*1024**3,
            summary_maximum_bytes=8*1024**3, growths=0)
        path = self.visualiser/'.storage-budget.json'
        path.write_text(json.dumps(budget)); path.chmod(0o600)
        before = path.read_bytes(), path.stat().st_mtime_ns
        result = self.reader()()['allowances']
        self.assertEqual(result, dict(visualiser_bytes=16*1024**3,
            summary_bytes=8*1024**3, download_bytes=1024**3))
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_invalid_visualiser_status_returns_unknown_allowances_without_cleanup(self):
        path = self.visualiser/'.storage-budget.json'
        path.write_text('{"invalid":"secret path /private/raw.csv"}')
        path.chmod(0o600)
        result = self.reader()()
        self.assertIsNone(result['allowances']['visualiser_bytes'])
        self.assertIsNone(result['allowances']['summary_bytes'])
        self.assertTrue(path.exists())
        self.assertNotIn('private/raw', json.dumps(result))

    def test_configured_backups_are_measured_and_bound_to_their_actual_filesystem(self):
        backup = dict(available=True, configured=True, pending_stage=None,
            last_success_at=None, overdue=True, remote_configured=False, minimum_free_bytes=1024**3)
        reader = self.reader()
        with patch.object(storage, '_backup_snapshot', return_value=(self.backups, backup)):
            result = self.settled(reader)
        area = self.areas(result)['backups']
        self.assertTrue(area['complete'])
        self.assertEqual(area['bytes'], self.files['backup'].stat().st_blocks*512)
        self.assertIsNotNone(area['filesystem'])
        self.assertIn('backups', next(row for row in result['filesystems']
                                     if row['id'] == area['filesystem'])['areas'])

    def test_backup_status_is_allowlisted_without_paths_credentials_or_diagnostics(self):
        from . import backup_schedule
        config = dict(_digest='fake', backup=dict(directory=str(self.backups),
            minimum_free_gib=1, interval_hours=24, retry_minutes=30), remote=dict(identity='/secret/key'))
        stamp = datetime.now(timezone.utc).isoformat()
        status = dict(format=backup_schedule.FORMAT, current=None, incident=None,
            last_success=dict(config='fake', finished_at=stamp, credential='not-for-browser',
                              path='/secret/recovery.tar.gpg'))
        (self.backups/'status.json').write_text(json.dumps(status))
        (self.backups/'status.json').chmod(0o600)
        pointer = self.root/'backup.toml'; pointer.write_text('fake configuration')
        with patch.object(backup_schedule, 'load_config', return_value=config):
            root, result = storage._backup_snapshot(pointer)
        self.assertEqual(root, self.backups)
        self.assertEqual(result, dict(available=True, configured=True, pending_stage=None,
            last_success_at=stamp, overdue=False, remote_configured=True, minimum_free_bytes=1024**3))
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('credential', json.dumps(result))

    def test_oversized_backup_metadata_is_not_read_and_remains_unavailable(self):
        from . import backup_schedule
        config = dict(backup=dict(directory=str(self.backups)))
        path = self.backups/'status.json'; path.write_bytes(b'x'*(128*1024+1))
        pointer = self.root/'backup.toml'; pointer.write_text('fake configuration')
        with patch.object(backup_schedule, 'load_config', return_value=config), \
             patch.object(backup_schedule, 'load_status', side_effect=AssertionError('oversized')):
            root, result = storage._backup_snapshot(pointer)
        self.assertEqual(root, self.backups)
        self.assertTrue(result['configured'])
        self.assertFalse(result['available'])
        self.assertIsNone(result['last_success_at'])

    def test_missing_backup_configuration_is_not_mistaken_for_an_empty_backup(self):
        root, result = storage._backup_snapshot(self.root/'missing.toml')
        self.assertIsNone(root)
        self.assertFalse(result['configured'])
        self.assertFalse(result['available'])
        self.assertIsNone(result['overdue'])


if __name__ == '__main__':
    unittest.main()
