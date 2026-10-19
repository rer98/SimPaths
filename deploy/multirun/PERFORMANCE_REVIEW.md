<!-- (C) Copyright 2026, by Ross Richardson

Live operator controls, sampled resource evidence and future allocation review.
@author ross richardson
-->

# Performance review without interrupting jobs

## Current decisions

- Keep the original working-storage allowance as the basis of the ceiling.
  During testing, allow up to **2 times that allowance**, or 100% extra.
- Keep **25% growth steps** initially. Each step is calculated from the current
  allowance; the ceiling remains fixed relative to the original allowance.
- Permit finite decimal multipliers such as `1.3`, in the existing range 1–4.
  TOML uses `storage_multiplier = 1.3`; the local flag is
  `--recovery-storage-multiplier 1.3`. The default remains 2.
- Retain administrator growth notices and the durable recovery-event history.
- Measure absolute resource demand on the VPS before reducing starting allowances
  or growth steps. Review data informs operator-approved profiles; it does not
  change accepted jobs or automatically tune the scheduler.

Decimal caps use exact decimal multiplication and round **down** to whole MiB,
so rounding cannot exceed the requested ceiling. Fractional policies carry
`jasmine.resource-recovery.v2`; existing integer policies retain version 1 and
their exact frozen limits. An integral decimal such as `2.0` normalises to 2.
Changing the operator's defaults affects newly reviewed work, not queued/running
submissions. Broker and pool ceilings still apply.

## Why measure before trying 10% steps?

At the current 85% trigger, an 8 GiB allowance has approximately 1.2 GiB remaining
at exactly that threshold, whichever growth increment is selected. A 10% step
would add 0.8 GiB to the first allowance; a 25% step adds 2 GiB. Smaller steps
need less additional capacity and may let more configurations continue when disk
is scarce. They also leave less space after an increase and can require more
updates during a sustained burst. Polling and quota-update latency matter as well
as the increment. No polling threshold guarantees that an arbitrary burst fits.

Our retained-output totals do not establish the largest temporary file or the
largest short-lived burst. Input copies, native snapshots and database writes
need working space too; existing preflight estimates include initial copies.
Use a **five-second review interval** for the initial burst investigation. It
measures positive net workspace growth between readings, not every file write or
largest individual file. A file created and deleted between readings can be
missed. Do not infer a probability of quota failure from these preliminary data.

**To do after representative VPS measurements:** compare 10% and 25% increments
under real output workloads, including smaller starting allowances, repetitions,
input staging and busy disk conditions. Assess whether a minimum increment or a
write-rate-based safety margin is warranted. Changing to 10% is supported by the
existing setting, but is not enabled by this work.

## Install once, then use two independent switches

Install this application update and additive batch migration **031** using the
normal controlled deployment procedure. Migration 027 remains unchanged. Later
review switches require no restart, queue drain, cancellation or model relaunch.

| Control | Default | Effect on running work |
| --- | --- | --- |
| External review | Off; 15-second samples when enabled | Starts/stops readings live |
| Detailed Java review for new launches | Off; 15-second timer when enabled | Retains the mode chosen at each process's launch |
| Essential admission, growth, timeout and recovery checks | Existing safety policy | Independent of review |
| Bounded lifecycle and chart/cache timings | Enabled with this code | Continues without detailed profiling |

The private collector polls controls every five seconds. Slow native/database
calls can delay acknowledgement. Each external interval change starts a new
window; measurements never bridge disabled time. Status distinguishes requested
controls, acknowledgement, error types and dropped/pending records.

The Java switch is read at **actual process launch**, including queued jobs and
each retry. Disabling it changes future launches; instrumented processes continue
until completion. Processes launched with Java review off do not load its observer,
start its thread or create detailed logs. Java could support stopping a running
observer, but an in-process stop/restart control is not implemented in this version.

### Operator commands

Run in the installed SimPaths checkout/environment as the private service account:

```bash
# Start external readings without interrupting jobs.
python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml enable --sample-seconds 15

# Prepare once for EACH verified immutable model JAR, outside user jobs.
# Requires a compatible JDK with javac.
python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml prepare-java \
  --model-jar /path/to/verified/model.jar

# Include detailed Java observations in subsequently launched processes.
python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml java-enable --sample-seconds 15

# Supply non-secret provider/layout context; an optional deployment SHA is supported.
python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml platform \
  --provider Contabo --plan VPS12 --storage-kind ssd

python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml status --json --limit 1000
python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml report --html --limit 2000 \
  --output /path/to/private-review.html

# Stop optional external sampling and omit detailed Java from future processes.
python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml java-disable
python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml disable
```

For an existing laptop service, replace `--config ...` with
`--frontend /path/to/JAS-mine-web --state /path/to/private/multirun-state`.
Reports can use `--dsn-file`, `--pool` and `--schema` for an existing database.
Controls do not contact Docker, PostgreSQL or SMTP or create missing service state.
Sampling intervals accept 5–300 seconds. Use five seconds for a bounded burst
investigation, then measure its cost before keeping it enabled.

Older model releases use a separately compiled, checksum-verified small observer
JAR. No compilation occurs during a user's launch. The scientific JAR, inputs,
seeds, main, heap and container allowances remain unchanged. An unsupported or
unavailable observer falls back to the ordinary command and records that mode.
Verify actual launch mode, rather than assuming the switch proves activation.
The detailed wrapper covers MultiRun simulation and queued input-preparation JVMs.
Interactive SingleRun supplies external group measurements; its long-lived
application JVM does not gain internal profiling from this change.

## Implemented improvements

### 1. Resources and progress on a common timeline

Recent observations retain CPU time/quota, total/working cgroup RAM, known disk
quotas, throttling, I/O and read duration where available. Reports join the existing
confirmed resource-change journal, split intervals by observed phase/year/repetition
and confirmed allowance, and mark gaps, transitions and resets. They never assign
an uncertain interval's CPU time to its final quota. Native timestamps reflect the
actual reading; operational and review collectors share a one-second snapshot
cache to avoid duplicate Docker/quota reads.

### 2. Optional Java memory and progress evidence

Numeric records include heap used/committed/maximum, non-heap use, GC count/time,
process CPU and setup, annual, output, between-repetition and final markers. The
model thread reads collection size at existing annual messages. Timer samples
reuse the last population observation and its timestamp, without traversing or
concurrently inspecting model collections. Preparation without annual messages
still supplies memory/CPU/GC and start/end evidence.

There is no forced GC, heap dump, stack tracing or raw-output parsing. GC counter
time is not application pause duration. Observed population and sampled peaks are
not exact lifetime maxima. A 64-entry queue feeds two rotating files, each at most
1 MiB; full queues drop evidence instead of waiting for space on the model thread.
Annual MXBean snapshots still cost model-thread CPU, to be measured by cost trials.

### 3. The complete path to usable results

Basic events relate queue/input preparation/model timing to Visualiser task
queueing, intermediate-summary readiness, comparisons, publication, indexing and
ready timestamps, including cache hits/misses and raw-processing costs. They remain
available without detailed review. Reports separate input wait from subsequent
queue time and first observed charts from all required baseline pairs. Successful
input preparation is not counted as a completed simulation. Chart times record
historical generation, not current retention or access permission.
Retry queue time begins after the preceding attempt finishes, excluding its
execution time. Submission-to-admission elapsed time is also retained separately.

External review samples scoped Visualiser groups and linked active SingleRun
containers, verifying session/deployment labels. Available processing-group counters
are also captured before cleanup. A short unobserved SingleRun session cannot
acquire a retrospective peak. Existing cache eviction/reprocessing counters help
explain expensive reconstruction; measurements do not read scientific rows.

### 4. Host context and comparable workloads

Readings include available CPU/steal, memory, disk-device I/O, CPU/memory/I/O
pressure, PostgreSQL connections/lock waiters/temp-I/O counters, service cgroups
and bounded HTTP latency histograms. Missing counters/permissions, disabled database
I/O timing and VM host uncertainty remain limitations. An advertised virtual CPU
name does not establish exclusive physical cores.

Model/runtime, Visualiser, web-code and operator deployment fingerprints accompany
a keyed fingerprint of immutable inputs, scientific choices and seeds. Display
labels do not affect it. Keep provider/plan/layout descriptors accurate and compare
matched fingerprints, output settings and complete outcomes. Exports retain numeric
metadata and opaque references, without seeds, scientific values, person rows,
credentials, owner addresses or private paths. They are private operator reports.
The automatic application-code fingerprint also covers deployed SimPaths
adapters, configuration, resource policy and runner code. Optional corrupt platform
descriptors leave that context unknown rather than preventing service startup.

### 5. Coverage, failures and bounds

Recent attempts appear even when periodic sampling missed them, including failed
work and retries. Reports include first/last observations and last-sample-to-exit
gaps. External coverage is absent or partial; periodic readings cannot certify full
coverage. Java coverage is complete only for an unbroken initial-to-final sequence
with no recorded loss; that still does not imply an exact peak. Available cumulative
Java/cgroup counters are distinguished from sampled peaks. Missing totals are unknown.

| Record or buffer | Bound |
| --- | --- |
| Recent detail | 7 days OR 50,000 rows per pool, whichever is reached first |
| Subject / attempt-window summaries | Default 90 days / 10,000 rows per table |
| Pending trace / write batch | 2,048 / 128 observations |
| Active models / registered processing groups | 128 / 128 |
| Linked active SingleRun sessions sampled per pass | 16 |
| Private report | 2,000 rows per table; truncation labelled |

`enable --retention-days N --max-rows N` controls longer summaries (1–365 days,
100–100,000 rows). Successful writes apply caps/expiry; an idle disabled collector
does not prune continuously. Pending/basic events can flush after disable. Shutdown
joins collection and performs a bounded drain. Outages/full buffers can lose evidence;
status reports pending/dropped counts and error types. Writes use a separate advisory
lock and short timeouts, without the scheduler pool-row lock. Immutable event IDs
prevent duplicate aggregation after lost commit acknowledgements. Review never
authorises admission, resource release, recovery or CPU/memory/disk changes.

### 6. Native proofs and measurement costs

`performance_monitoring_proof.py` checks live controls, real same-container/PID Docker
CPU changes, transition coverage, runtime shutdown and PostgreSQL backup/restore
with fictional work. Local Java tests compare unchanged output/errors and disabled
launches; numeric/file tests cover gaps, resets, bounded logs and private fingerprints.
These functional checks do not establish monitoring cost on a loaded VPS.

`monitoring_cost_trials.py` counterbalances off/external/full at matched allocations
and inputs using tiny fictional Java CPU/allocation/I/O work through the real queue
and executor. It records container/driver time, independent reference CPU/peak
readings, collector cost and labelled HTML/JSON. Compilation and control
acknowledgement occur before timing. Reference readings are present in every
fictional condition and occur just before JVM shutdown.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python deploy/multirun/monitoring_cost_trials.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --sample-seconds 15 --rounds 3 \
  --output "/tmp-codex/monitoring-cost-$(date +%Y%m%d-%H%M%S)"
```

Repeat separately at five seconds. Keep networking enabled for isolated dependency
installation; the runtime is local. Do not overlap quiet scientific benchmarks.
Default fictional trials need 1 GiB free disk. They have no XFS quota broker, so
quota storage is unknown and broker latency is not qualified by this fixture.

`--scientific --end-year 2021` explicitly opts into one ordinary public simulation
per condition/round, using the population in the calibration package. It needs
15 GiB free disk, a quiet machine and substantially more time. It excludes old laptop
probe wrappers and uses the production observer. Output validation is retained;
validation alone is not proof that all numeric scientific results matched.

**Host measurements still to do:** repeated quiet cost trials, representative
public-data off/on comparisons, and VPS mixed-load tests with the real quota broker.
Measure web/SingleRun responsiveness, chart delays, disk stalls and completion
distributions at different host loads. No overhead percentage, automatic tuning or
improved allocation is claimed before results. Observations inform reviewed profiles
for new work; they do not establish causal speed differences on their own.

## Backup and recovery

Schema dumps include both new tables. Offline state copies retain review controls;
online backup retains the workload-fingerprint key and platform descriptors and
omits live controls/transient observer files. Check/reapply controls on a restored
host and prepare the small observer again as needed. Metadata confers no access to
raw results or retained Visualiser data.

## Administrator alerts

Confirmed storage growth still creates durable recovery events and configured
`resource_admin` notices independently of review. Delivery requires the normal
notification switch, administrator recipient and SMTP settings. Frequent growth
prompts workload/profile review; it does not prove all starting limits are too small.

## Validation record

The 17 October external-review proof passed 179 checks, followed by seven checks
for sampling-interval metadata. Those reports predate migration031 and the observer.
The new 19 October focused PostgreSQL proof passed **33 checks**, with cleanup:
[/tmp-codex/performance-monitoring-native-20261019-115211/report.json](/tmp-codex/performance-monitoring-native-20261019-115211/report.json).
The subsequent native runtime/recovery proof passed **50 checks**, without skips,
with Docker/database cleanup confirmed:
[/tmp-codex/performance-runtime-final-20261019-121537/model-proof/report.json](/tmp-codex/performance-runtime-final-20261019-121537/model-proof/report.json).
It includes the longer-lived cache-reprocessing counters. Monitoring-cost results
are recorded separately. The timing follow-up passed **28 PostgreSQL checks**:
[/tmp-codex/performance-timing-native-20261019-122218/report.json](/tmp-codex/performance-timing-native-20261019-122218/report.json).
This covers retry queue timing; a local slow-reader regression confirms snapshot
reuse is timed from read completion.

The one-round, five-second fictional cost smoke also passed, with identical
result fingerprints in all three modes and cleanup confirmed:
[/tmp-codex/monitoring-cost-smoke-20261019-121732/model-proof/report.json](/tmp-codex/monitoring-cost-smoke-20261019-121732/model-proof/report.json).
It verifies the harness, not a monitoring-overhead percentage. Counterbalanced
quiet and representative host measurements remain outstanding as described above.
