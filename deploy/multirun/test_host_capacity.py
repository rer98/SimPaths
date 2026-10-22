"""(C) Copyright 2026, by Ross Richardson

Filesystem-only host capacity checks, private delivery state and captured mail.
No Docker, database, scientific runs, real mail or installed service changes.
@author ross richardson
"""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import errno
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deploy._workflow import frontend_path
from . import host_capacity as host


def sample(percent=20, *, device=1, inodes=10):
    return dict(device=device, total_bytes=100*1024**3,
        used_bytes=round(percent*1024**3), available_bytes=round((100-percent)*1024**3),
        space_percent=percent, inode_total=1000 if inodes is not None else None,
        inode_free=round((100-inodes)*10) if inodes is not None else None,
        inode_percent=inodes)


class HostCapacityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='simpaths-capacity-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state, self.runtime = self.root/'state', self.root/'runtime'
        self.file = self.root/'capacity.toml'
        self.text = f'''[monitor]
enabled = true
frontend = "{frontend_path()}"
[alerts]
enabled = true
recipient = "operator@example.invalid"
[[filesystems]]
name = "data"
path = "/simpaths-data"
'''
        self.file.write_text(self.text); self.file.chmod(0o600)
        self.config = host.load_config(self.file)
        self.now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
        self.samples, self.messages = {'data':sample()}, []
        self.reader = Mock(side_effect=self.read)

    def read(self, item, mounts):
        value = self.samples[item['name']]
        if isinstance(value, Exception):
            raise value
        return dict(value)

    def send(self, config, state, current):
        self.messages.append(dict(notice=host.notification(config, state, current),
            current=deepcopy(current), id=state['pending_id']))

    def run_monitor(self, minutes=0, **changes):
        options = dict(now=self.now+timedelta(minutes=minutes), reader=self.reader,
                       mount_reader=lambda:{}, sender=self.send, runtime=self.runtime)
        options.update(changes)
        return host.run(self.config, self.state, **options)

    def saved(self):
        return host.load_state(self.state, 'simpaths-online', self.runtime)

    def test_disabled_monitor_performs_no_probes_creates_no_state_and_sends_no_mail(self):
        self.config['monitor']['enabled'] = False
        result = self.run_monitor()
        self.assertFalse(result['enabled'])
        self.reader.assert_not_called()
        self.assertEqual(self.messages, [])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.runtime.exists())

    def test_configuration_is_strict_private_and_does_not_access_watched_filesystems(self):
        with patch.object(host, 'probe', side_effect=AssertionError('No measurements at configuration load')):
            config = host.load_config(self.file)
        self.assertEqual((config['monitor']['warning_percent'], config['monitor']['recovery_percent']), (85, 80))
        for changed in (self.text.replace('enabled = true', 'enabled = 1', 1),
                self.text.replace('[alerts]', 'warning_percent = 101\n[alerts]'),
                self.text.replace('[alerts]', 'recovery_percent = 85\n[alerts]'),
                self.text.replace('[alerts]', 'mail_spacing_minutes = 0\n[alerts]'),
                self.text.replace('[alerts]', 'unexpected = true\n[alerts]'),
                self.text.replace('operator@example.invalid', 'not an email'),
                self.text.replace('/simpaths-data', '../relative'),
                self.text+'\n[[filesystems]]\nname="data"\npath="/other"\n'):
            with self.subTest(text=changed), self.assertRaises(ValueError):
                self.file.write_text(changed)
                host.load_config(self.file)
        self.file.write_text(self.text); self.file.chmod(0o644)
        with self.assertRaises(ValueError):
            host.load_config(self.file)
        self.file.chmod(0o600)
        link = self.root/'linked'; link.symlink_to(self.file)
        with self.assertRaises(ValueError):
            host.load_config(link)

    def test_statvfs_uses_fragment_size_and_service_available_space_without_reading_files(self):
        raw = SimpleNamespace(f_blocks=1000, f_bfree=200, f_bavail=100, f_frsize=4096,
                              f_files=100, f_ffree=10)
        item = dict(name='data', path=str(self.root), require_mount=False)
        with patch.object(os, 'statvfs', return_value=raw), \
                patch.object(os, 'walk', side_effect=AssertionError('Do not scan')), \
                patch.object(Path, 'rglob', side_effect=AssertionError('Do not scan')):
            result = host.probe(item, {})
        self.assertEqual(result['total_bytes'], 1000*4096)
        self.assertEqual(result['available_bytes'], 100*4096)
        self.assertAlmostEqual(result['space_percent'], 100*800/900)
        self.assertEqual(result['inode_percent'], 90)
        raw.f_files = 0
        with patch.object(os, 'statvfs', return_value=raw):
            self.assertIsNone(host.probe(item, {})['inode_percent'])

    def test_missing_expected_mount_never_reports_healthy_parent_filesystem(self):
        item = dict(name='data', path=str(self.root), require_mount=True)
        with patch.object(os, 'statvfs', side_effect=AssertionError('No parent fallback')), \
                self.assertRaises(host.MissingMount):
            host.probe(item, {})
        self.samples['data'] = host.MissingMount('PRIVATE_CONFIGURATION')
        result = self.run_monitor()
        self.assertEqual(result['active']['data']['reasons'], ['unavailable'])
        self.assertIn('MissingMount', self.messages[0]['notice'][1])
        self.assertNotIn('PRIVATE_CONFIGURATION', json.dumps(result))

    def test_mount_inventory_is_bounded_and_decodes_escaped_mount_names(self):
        source = self.root/'mountinfo'
        source.write_text('29 23 8:1 / /mnt/data\\040one rw - ext4 /dev/sda1 rw\n')
        result = host.mounted_devices(source)
        self.assertEqual(result, {'/mnt/data one':{os.makedev(8, 1)}})
        source.write_bytes(b'x'*(1024**2+1))
        with self.assertRaises(ValueError):
            host.mounted_devices(source)

    def test_healthy_checks_use_one_mount_inventory_and_do_not_scan_files_or_import_services(self):
        mounts = Mock(return_value={})
        with patch.object(os, 'walk', side_effect=AssertionError('No scan')), \
                patch.object(Path, 'rglob', side_effect=AssertionError('No scan')), \
                patch.object(host, 'send', side_effect=AssertionError('No SMTP')):
            result = self.run_monitor(mount_reader=mounts)
        mounts.assert_called_once()
        self.assertEqual(self.reader.call_count, 1)
        self.assertEqual(result['active'], {})
        self.assertEqual(self.messages, [])
        self.assertLess((self.state/host.STATE).stat().st_size, 4096)

    def test_warning_deduplicates_across_restarts_and_clears_only_below_recovery_threshold(self):
        self.samples['data'] = sample(85)
        self.run_monitor()
        self.samples['data'] = sample(86)
        self.run_monitor(5)
        self.samples['data'] = sample(82)
        self.run_monitor(35)
        self.assertEqual(len(self.messages), 1)
        self.assertTrue(self.saved()['observations'][0]['space_warning'])
        self.samples['data'] = sample(80)
        self.run_monitor(40)
        self.assertEqual(len(self.messages), 1)
        self.samples['data'] = sample(79)
        result = self.run_monitor(45)
        self.assertEqual(len(self.messages), 2)
        self.assertIn('capacity recovered', self.messages[-1]['notice'][0])
        self.assertEqual(result['notified'], {})
        self.run_monitor(80)
        self.assertEqual(len(self.messages), 2)

    def test_multiple_allocations_are_combined_and_new_changes_wait_for_delivery_spacing(self):
        self.config['filesystems'].append(dict(name='backups', path='/simpaths-backups', require_mount=True))
        self.samples.update(data=sample(90), backups=sample(88, device=2))
        self.run_monitor()
        self.assertEqual(set(self.messages[0]['current']), {'data', 'backups'})
        self.samples['backups'] = sample(20, device=2)
        result = self.run_monitor(5)
        self.assertTrue(result['mail_pending'])
        self.assertEqual(len(self.messages), 1)
        self.run_monitor(30)
        self.assertEqual(len(self.messages), 2)
        self.assertIn('backups: recovered.', self.messages[-1]['notice'][1])

    def test_aliases_on_one_filesystem_produce_one_warning_and_are_not_double_counted(self):
        self.config['filesystems'].append(dict(name='alias', path='/alias', require_mount=True))
        self.samples.update(data=sample(90), alias=sample(90))
        result = self.run_monitor()
        self.assertEqual(list(result['active']), ['data'])
        self.assertEqual(result['observations'][1]['duplicate_of'], 'data')

    def test_inode_pressure_warns_without_a_byte_capacity_warning(self):
        self.samples['data'] = sample(20, inodes=90)
        result = self.run_monitor()
        self.assertEqual(result['active']['data']['reasons'], ['inodes'])
        self.assertIn('File entries (inodes): 90.0%', self.messages[0]['notice'][1])
        self.samples['data'] = sample(20, inodes=79)
        self.run_monitor(30)
        self.assertEqual(len(self.messages), 2)

    def test_unavailable_inode_counters_do_not_falsely_clear_an_existing_warning(self):
        self.samples['data'] = sample(20, inodes=90)
        self.run_monitor()
        self.samples['data'] = sample(20, inodes=None)
        result = self.run_monitor(30)
        self.assertEqual(result['active']['data']['reasons'], ['inodes'])
        self.assertEqual(len(self.messages), 1)
        self.run_monitor(24*60)
        self.assertIn('earlier inode warning remains unresolved', self.messages[-1]['notice'][1])

    def test_unavailable_readings_preserve_warning_hysteresis_until_capacity_is_known_again(self):
        self.samples['data'] = sample(90)
        self.run_monitor()
        self.samples['data'] = OSError('PRIVATE_DATASET_PATH')
        self.run_monitor(30)
        self.samples['data'] = sample(82)
        self.run_monitor(60)
        self.assertEqual(self.messages[-1]['current']['data']['reasons'], ['space'])
        self.assertNotIn('PRIVATE_DATASET_PATH', json.dumps(self.messages))

    def test_daily_reminder_is_bounded_and_healthy_checks_send_nothing(self):
        self.samples['data'] = sample(90)
        self.run_monitor()
        self.run_monitor(60)
        self.run_monitor(24*60-1)
        self.assertEqual(len(self.messages), 1)
        self.run_monitor(24*60)
        self.assertEqual(len(self.messages), 2)
        self.assertNotEqual(self.messages[0]['id'], self.messages[1]['id'])

    def test_smtp_failure_is_sanitized_and_retries_the_same_notice_after_thirty_minutes(self):
        self.samples['data'] = sample(90)
        sender = Mock(side_effect=RuntimeError('SMTP_PASSWORD=PRIVATE'))
        result = self.run_monitor(sender=sender)
        self.assertEqual(result['mail_error'], 'RuntimeError')
        reference = result['pending_id']
        self.assertNotIn('SMTP_PASSWORD', json.dumps(result))
        self.run_monitor(5)
        self.assertEqual(self.messages, [])
        self.run_monitor(30)
        self.assertEqual(self.messages[0]['id'], reference)
        self.assertIsNone(self.saved()['mail_error'])

    def test_recovery_before_any_warning_was_delivered_discards_stale_notification(self):
        self.samples['data'] = sample(90)
        self.run_monitor(sender=Mock(side_effect=OSError('private mail failure')))
        self.samples['data'] = sample(20)
        result = self.run_monitor(30)
        self.assertFalse(result['mail_pending'])
        self.assertEqual(self.messages, [])

    def test_disabled_mail_collects_capacity_and_delivers_when_enabled_without_job_restart(self):
        self.config['alerts']['enabled'] = False
        self.samples['data'] = sample(90)
        self.assertTrue(self.run_monitor()['mail_pending'])
        self.assertEqual(self.messages, [])
        self.config['alerts']['enabled'] = True
        self.run_monitor(5)
        self.assertEqual(len(self.messages), 1)

    def test_full_persistent_storage_uses_small_runtime_fallback_and_recovers_saved_delivery_state(self):
        self.samples['data'] = sample(90)
        original = host.atomic_json
        def full(file, value):
            if file.parent == self.state:
                raise OSError(errno.ENOSPC, 'PRIVATE_STATE_LOCATION')
            return original(file, value)
        with patch.object(host, 'atomic_json', side_effect=full):
            first = self.run_monitor()
            self.assertEqual(first['state_store'], 'runtime')
            self.run_monitor(5)
        self.assertEqual(len(self.messages), 1)
        self.assertLess((self.runtime/host.STATE).stat().st_size, 4096)
        final = self.run_monitor(10)
        self.assertEqual(final['state_store'], 'persistent')
        self.assertFalse((self.runtime/host.STATE).exists())
        self.assertEqual(len(self.messages), 1)

    def test_overlapping_invocations_are_rejected_without_sending_duplicate_mail(self):
        host.private_directory(self.runtime)
        with host.locked(self.runtime), self.assertRaises(BlockingIOError):
            self.run_monitor()
        self.assertEqual(self.messages, [])

    def test_persistent_lock_excludes_invocations_with_different_runtime_arguments(self):
        host.private_directory(self.state)
        with host.locked(self.state), self.assertRaises(BlockingIOError):
            self.run_monitor()
        self.assertEqual(self.messages, [])

    def test_clock_moving_backwards_does_not_defer_failed_delivery_indefinitely(self):
        self.samples['data'] = sample(90)
        self.run_monitor(sender=Mock(side_effect=OSError()))
        self.run_monitor(-120)
        self.run_monitor(-115)
        self.assertEqual(self.messages, [])
        self.run_monitor(-90)
        self.assertEqual(len(self.messages), 1)

    def test_invalid_reading_does_not_hide_an_alias_that_has_valid_metadata(self):
        self.config['filesystems'].append(dict(name='alias', path='/alias', require_mount=True))
        self.samples['alias'] = sample(90)
        # Avoid converting NaN in the fixture's otherwise real byte counts.
        self.samples['data'] = dict(sample(90), space_percent=float('nan'))
        result = self.run_monitor()
        self.assertEqual(result['active']['data']['reasons'], ['unavailable'])
        self.assertEqual(result['active']['alias']['reasons'], ['space'])

    def test_state_is_private_bounded_and_cannot_follow_links(self):
        self.run_monitor()
        state = self.state/host.STATE
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        state.chmod(0o640)
        with self.assertRaises(ValueError):
            self.saved()
        state.chmod(0o600)
        duplicate = self.root/'duplicate'; os.link(state, duplicate)
        with self.assertRaises(ValueError):
            self.saved()
        duplicate.unlink()
        state.unlink(); state.symlink_to(self.file)
        with self.assertRaises(ValueError):
            self.saved()

    def test_cli_check_and_status_are_read_only_and_do_not_send_mail(self):
        before = set(self.root.iterdir())
        for command in ('check', 'status'):
            output = io.StringIO()
            with patch.object(host, 'collect', return_value=[]), \
                    patch.object(host, 'send', side_effect=AssertionError('No mail')), redirect_stdout(output):
                self.assertEqual(host.main([command, '--config', str(self.file), '--state', str(self.state)]), 0)
            json.loads(output.getvalue())
            self.assertEqual(set(self.root.iterdir()), before)

    def test_cli_errors_never_expose_private_exception_text(self):
        output = io.StringIO()
        with patch.object(host, 'load_config', side_effect=RuntimeError('PRIVATE_SMTP_PASSWORD')), \
                redirect_stderr(output):
            self.assertEqual(host.main(['run', '--config', str(self.file)]), 1)
        self.assertNotIn('PRIVATE_SMTP_PASSWORD', output.getvalue())

    def test_explicit_test_mail_has_a_test_subject_and_does_not_probe_or_change_state(self):
        prior = list(sys.path); self.addCleanup(lambda:setattr(sys, 'path', prior))
        sys.path.insert(0, str(frontend_path()))
        before = set(self.root.iterdir())
        with patch.dict(os.environ, {'SMTP_FROM_EMAIL':'sender@example.invalid'}, clear=True), \
                patch('jasmine_web.contact.deliver_email') as deliver, \
                patch.object(host, 'collect', side_effect=AssertionError('No capacity probes')), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(host.main(['test-mail', '--config', str(self.file)]), 0)
        self.assertIn('host capacity email test', deliver.call_args.args[0]['Subject'])
        self.assertEqual(set(self.root.iterdir()), before)

    def test_disabled_mail_cannot_send_an_explicit_test_message(self):
        self.file.write_text(self.text.replace('[alerts]\nenabled = true', '[alerts]\nenabled = false'))
        with patch.object(host, 'send', side_effect=AssertionError('No SMTP')), redirect_stderr(io.StringIO()):
            self.assertEqual(host.main(['test-mail', '--config', str(self.file)]), 1)

    def test_existing_smtp_helper_receives_only_the_combined_administrator_message(self):
        prior = list(sys.path); self.addCleanup(lambda:setattr(sys, 'path', prior))
        sys.path.insert(0, str(frontend_path()))
        self.samples['data'] = sample(90)
        self.run_monitor(sender=Mock(side_effect=OSError()))
        state = self.saved()
        current = host.active(state['observations'])
        with patch.dict(os.environ, {'SMTP_FROM_EMAIL':'sender@example.invalid'}, clear=True), \
                patch('jasmine_web.contact.deliver_email') as deliver:
            host.send(self.config, state, current)
        message = deliver.call_args.args[0]
        self.assertEqual(message['To'], 'operator@example.invalid')
        self.assertIn(state['pending_id'], message['Message-ID'])
        self.assertIn('90.0% used', message.get_content())

    def test_real_local_filesystem_metadata_probe_needs_no_directory_scan_or_network(self):
        config = deepcopy(self.config)
        config['filesystems'] = [dict(name='root', path='/', require_mount=True)]
        with patch.object(Path, 'rglob', side_effect=AssertionError('No traversal')):
            rows = host.collect(config)
        self.assertTrue(rows[0]['available'], rows)
        self.assertGreater(rows[0]['total_bytes'], 0)
        self.assertGreaterEqual(rows[0]['space_percent'], 0)


if __name__ == '__main__':
    unittest.main()
