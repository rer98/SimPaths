"""(C) Copyright 2026, by Ross Richardson
Read-only, bounded Docker memory evidence for a uniquely labelled interactive trial.
@author ross richardson
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time


GIB = 1024 ** 3


def _integer(value, name):
    if type(value) is not int or value < 0:
        raise ValueError('Missing or invalid ' + name)
    return value


class ContainerMemorySampler:
    """The caller owns the Docker client and must stop before removing its container.

    start() creates an exclusive JSONL evidence file and begins two-second polling.
    capture() also samples immediately at important phase boundaries. stop() returns
    a report; callers must require its ``passed`` field, not merely a nonzero peak.
    Use a Docker client with a bounded HTTP timeout. This class never closes it or
    changes containers. Missing measurements and long gaps invalidate the evidence.
    """

    def __init__(self, client, model_id, output_path, *, expected_memory_bytes=5 * GIB,
                 expected_cpu_nanos=2_000_000_000, expected_memory_swap_bytes=None,
                 interval_seconds=2, max_samples=43_200):
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError('A unique nonempty model label is required')
        for value in (expected_memory_bytes, expected_cpu_nanos, max_samples):
            if type(value) is not int or value <= 0:
                raise ValueError('Expected limits and sample budget must be positive integers')
        if not 0 < interval_seconds <= 60 or max_samples > 43_200:
            raise ValueError('Invalid sampling bounds')
        self.client, self.model_id, self.path = client, model_id, Path(output_path)
        self.expected = {'Memory': expected_memory_bytes, 'NanoCpus': expected_cpu_nanos,
                         'MemorySwap': expected_memory_bytes if expected_memory_swap_bytes is None
                         else expected_memory_swap_bytes}
        if self.expected['MemorySwap'] != expected_memory_bytes:
            raise ValueError('Memory trial requires swap disabled')
        self.interval, self.max_samples = interval_seconds, max_samples
        self._stop = threading.Event()
        self._capture_lock = threading.Lock()
        self._thread = None
        self._started = self._last = self._first = None
        self._started_at = self._container_id = self._container_started = None
        self._errors = []
        self._count = self._polls = 0
        self._peak_raw = self._peak_working = self._peak_swap = None
        self._max_gap = 0
        self._result = None

    def _error(self, message):
        if message not in self._errors and len(self._errors) < 50:
            self._errors.append(message)

    def start(self):
        if self._started is not None:
            raise RuntimeError('Sampler already started')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('x', encoding='utf-8'):
            pass
        self.path.chmod(0o600)
        self._started, self._started_at = time.monotonic(), datetime.now(timezone.utc).isoformat()
        self._thread = threading.Thread(target=self._run, name='interactive-memory', daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            self.capture()
            if self._stop.wait(self.interval):
                break

    def capture(self):
        """Sample once, recording errors instead of letting a daemon exception pass unseen."""
        if self._started is None:
            raise RuntimeError('Sampler has not started')
        with self._capture_lock:
            if self._stop.is_set():
                return None
            self._polls += 1
            if self._polls > self.max_samples or time.monotonic() - self._started > 86_400:
                self._error('Sampling budget exhausted')
                self._stop.set()
                return None
            try:
                sample = self._read()
                if sample is None or self._stop.is_set():
                    return None
                now = time.monotonic()
                if self._last is not None:
                    self._max_gap = max(self._max_gap, now - self._last)
                    if now - self._last > max(10, 3 * self.interval):
                        self._error('Sampling gap exceeds the permitted interval')
                self._first = now if self._first is None else self._first
                self._last = now
                sample.update(timestamp=datetime.now(timezone.utc).isoformat(),
                              elapsed_seconds=now - self._started)
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(sample, sort_keys=True, allow_nan=False) + '\n')
                self._count += 1
                self._peak_raw = max(self._peak_raw or 0, sample['usage_bytes'])
                self._peak_working = max(self._peak_working or 0, sample['working_bytes'])
                if sample['swap_bytes'] is not None:
                    self._peak_swap = max(self._peak_swap or 0, sample['swap_bytes'])
                return sample
            except ValueError as error:
                self._error(str(error))
            except Exception as error:
                # Docker errors can contain daemon paths or credentials; only record type.
                self._error('Docker sampling failed: ' + type(error).__name__)
            return None

    def _read(self):
        containers = self.client.containers.list(all=True,
            filters={'label': 'jasmine.model_id=' + self.model_id})
        if not containers:
            if self._container_id is not None:
                raise ValueError('Observed container disappeared before sampling stopped')
            return None
        if len(containers) != 1:
            raise ValueError('More than one container has the trial model label')
        container = containers[0]
        container.reload()
        attrs = container.attrs
        if attrs.get('Config', {}).get('Labels', {}).get('jasmine.model_id') != self.model_id:
            raise ValueError('Container does not have the expected model label')
        if not isinstance(container.id, str) or not container.id:
            raise ValueError('Missing container identity')
        if self._container_id is not None and container.id != self._container_id:
            raise ValueError('Trial container was replaced')
        self._container_id = container.id
        host, state = attrs.get('HostConfig', {}), attrs.get('State', {})
        if any(type(host.get(key)) is not int or host[key] != value
               for key, value in self.expected.items()):
            raise ValueError('Container CPU, memory or swap limits differ from the trial settings')
        if type(state.get('OOMKilled')) is not bool or state['OOMKilled']:
            raise ValueError('Missing OOM status or container was OOM-killed')
        if _integer(attrs.get('RestartCount'), 'restart count') != 0:
            raise ValueError('Container restarted')
        if state.get('Status') == 'created' and self._count == 0:
            return None
        if state.get('Running') is not True or state.get('Paused') is True:
            raise ValueError('Trial container stopped or paused during sampling')
        started = state.get('StartedAt')
        if not isinstance(started, str) or not started:
            raise ValueError('Missing container start time')
        if self._container_started is not None and started != self._container_started:
            raise ValueError('Container start time changed')
        self._container_started = started
        stats = container.stats(stream=False)
        memory = stats.get('memory_stats', {})
        if _integer(memory.get('limit'), 'memory limit') != self.expected['Memory']:
            raise ValueError('Reported memory limit differs from the trial setting')
        usage = _integer(memory.get('usage'), 'memory usage')
        detail = memory.get('stats', {})
        cache_key = 'inactive_file' if 'inactive_file' in detail else 'total_inactive_file'
        inactive = _integer(detail.get(cache_key), 'inactive file cache')
        if inactive > usage:
            raise ValueError('Inactive file cache exceeds total memory usage')
        swap = detail.get('swap', detail.get('total_swap'))
        if swap is not None and _integer(swap, 'swap usage') != 0:
            raise ValueError('Trial used swap')
        cpu = stats.get('cpu_stats', {})
        total_cpu = cpu.get('cpu_usage', {}).get('total_usage')
        if total_cpu is not None:
            _integer(total_cpu, 'CPU time')
        return dict(container_id=container.id, usage_bytes=usage, inactive_file_bytes=inactive,
                    working_bytes=usage - inactive, cache_field=cache_key, swap_bytes=swap,
                    swap_disabled=True, oom_killed=False, restart_count=0,
                    cpu_total_nanoseconds=total_cpu, limits=self.expected)

    def stop(self, *, timeout=5):
        """Return bounded-stop evidence; an in-flight Docker request cannot pass silently."""
        if self._started is None:
            raise RuntimeError('Sampler has not started')
        if self._result is not None:
            return dict(self._result)
        self._stop.set()
        self._thread.join(timeout=max(0, min(timeout, 10)))
        if self._thread.is_alive():
            self._error('Sampler did not stop within its bounded wait')
        now = time.monotonic()
        if not self._count:
            self._error('No valid running-container samples were collected')
        if self._last is not None:
            self._max_gap = max(self._max_gap, now - self._last)
            if now - self._last > max(10, 3 * self.interval):
                self._error('Sampling gap before stop exceeds the permitted interval')
        self._result = dict(passed=not self._errors, errors=list(self._errors),
                            samples=self._count, polls=self._polls, container_id=self._container_id,
                            started_at=self._started_at, elapsed_seconds=now - self._started,
                            peak_usage_bytes=self._peak_raw, peak_working_bytes=self._peak_working,
                            peak_swap_bytes=self._peak_swap, maximum_gap_seconds=self._max_gap,
                            limits=dict(self.expected), sample_file=str(self.path))
        return dict(self._result)
