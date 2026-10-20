"""(C) Copyright 2026, by Ross Richardson

Nonempty retained Visualiser and monitoring fixtures for update/schema recovery.
Uses fictional aggregates and queued attachments; never sends mail or runs Java.
@author ross richardson
"""
from datetime import datetime
import hashlib
import json
from uuid import UUID


TABLES = ('performance_trace', 'performance_subjects', 'performance_export_parts',
          'performance_export_changes', 'performance_export_context',
          'performance_export_epochs', 'performance_export_sources',
          'performance_export_batches', 'performance_export_latest')


def numeric_value(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        body = bytes(value)
        return dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    if isinstance(value, datetime): return value.isoformat()
    if isinstance(value, UUID): return str(value)
    if isinstance(value, dict): return {k:numeric_value(v) for k,v in value.items()}
    if isinstance(value, (tuple, list)): return [numeric_value(v) for v in value]
    return value


def numeric_snapshot(queue):
    from psycopg import sql
    result = {}
    with queue._connection() as connection:
        for name in TABLES:
            query = sql.SQL('SELECT * FROM {}').format(sql.Identifier(queue.schema, name))
            rows = [json.dumps(numeric_value(dict(row)), sort_keys=True, allow_nan=False)
                    for row in connection.execute(query).fetchall()]
            result[name] = dict(rows=len(rows), sha256=hashlib.sha256(
                '\n'.join(sorted(rows)).encode()).hexdigest())
    return result


def attach(service, executor):
    from jasmine_web.batch.visualiser import Visualiser
    from jasmine_web.batch.policy import Resources
    from test_visualiser import Backend
    backend = Backend()
    backend.root = executor.root
    backend.supports_selective_views = True
    service.visualiser = Visualiser(service.results, backend,
        resources=Resources(1000, 1024, 16), storage_policy=dict(enabled=True,
            physical=lambda:dict(free_bytes=10**12, committed_bytes=0,
                                 growth_committed_bytes=0, reserve_bytes=1)))
    return service.visualiser


def seed(fixture, authentication):
    from deploy.multirun.test_backup_visualiser import NativeVisualiserRestoreTests
    from deploy.multirun.visualiser_summary_cache import SummaryCache
    from jasmine_web.batch import performance_review as review, performance_exports as exports
    from jasmine_web.batch.performance_trace import ReviewTrace
    from jasmine_web.batch.visualiser_storage import approve
    helper = NativeVisualiserRestoreTests()
    helper.f = fixture
    service = helper.service(fixture.q, fixture.state)
    token, csrf = authentication['token'], authentication['csrf']
    visualiser = service.visualiser
    key = visualiser.request(token, csrf, fixture.completed.job_id)['id']
    visualiser.tick()
    publication = visualiser.data_bytes(token, key)
    summary = fixture.root/'update-summary.json'
    summary.write_bytes(b'[{"year":2019,"scenario":"baseline","run":"606","metric_value":1}]')
    summary.chmod(0o600)
    identity = dict(owner=fixture.owner, configuration='baseline',
        execution=dict(job=fixture.completed.job_id, attempt=fixture.completed.attempt_id), seed='606')
    with SummaryCache(visualiser.root/'summaries').entry(identity) as entry:
        assert entry.publish(summary)
    approved = approve(visualiser.root, maximum_bytes=512*1024**2)

    settings = exports.Settings(enabled=True, mail_enabled=False)
    exports.configure(fixture.q, settings)
    trace = ReviewTrace(fixture.q, fixture.state)
    trace.emit('model', fixture.completed.attempt_id, 'launch', job=fixture.completed.job_id,
               java_mode='off', workload_fingerprint=trace.fingerprint(fixture.completed))
    trace.emit('model', fixture.completed.attempt_id, 'sample', cpu_ns=123, working_memory_bytes=2048)
    trace.flush(review.Settings())
    assert trace.write_error is None and trace.error is None and trace.dropped == 0
    exports.snapshot_context(fixture.q)
    assert exports.build_batch(fixture.q, settings, force=True)
    trace.emit('model', fixture.completed.attempt_id, 'sample', cpu_ns=321, working_memory_bytes=4096)
    trace.flush(review.Settings())
    assert trace.write_error is None and trace.error is None and trace.dropped == 0
    snapshot = numeric_snapshot(fixture.q)
    assert snapshot['performance_trace']['rows'] == 3
    assert snapshot['performance_export_parts']['rows'] > 0
    assert snapshot['performance_export_changes']['rows'] > 0
    return dict(key=key, publication_sha256=hashlib.sha256(publication).hexdigest(),
        summary_identity=identity, summary_sha256=hashlib.sha256(summary.read_bytes()).hexdigest(),
        approved_budget=approved, numeric_snapshot=snapshot)


def verify_restored(fixture, saved):
    from deploy.multirun.maintenance import GATE
    if (fixture.target/GATE).exists():
        raise ValueError('Activate the verified restore before opening retained caches')
    from deploy.multirun.visualiser_summary_cache import SummaryCache
    from jasmine_web.batch.visualiser_storage import budget
    root = fixture.target/'execution/visualiser-cache'
    assert budget(root) == saved['approved_budget']
    destination = fixture.root/'restored-update-summary.json'
    with SummaryCache(root/'summaries').entry(saved['summary_identity']) as entry:
        assert entry.restore(destination)
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == saved['summary_sha256']
    assert numeric_snapshot(fixture.target_q) == saved['numeric_snapshot']
    return dict(summary_reused=True, approved_budget_preserved=True,
        monitoring_records_preserved=True,
        retained_export_parts=saved['numeric_snapshot']['performance_export_parts']['rows'],
        unpacked_monitoring_changes=saved['numeric_snapshot']['performance_export_changes']['rows'])
