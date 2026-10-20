<!-- (C) Copyright 2026, by Ross Richardson

Live operator controls, sampled resource evidence and future allocation review.
@author ross richardson
-->

# Performance review without interrupting jobs

## Current decisions

- For the **first VM deployment and trial**, enable both external review and
  detailed Java review at a **15-second interval**. Prepare/verify the observer
  for each selected immutable model JAR before admitting test jobs, then check
  control acknowledgement and the actual Java launch receipt. Java review covers
  newly launched MultiRun and queued preparation processes; SingleRun contributes
  external measurements. This deployment choice uses the existing operator
  controls, leaving software defaults and local benchmark conditions unchanged.
- Enable [administrator monitoring archives](MONITORING_EXPORTS.md) before
  collection on that deployment; enable daily delivery after verifying the
  private administrator recipient and SMTP. Complete measurements remain
  independently collatable after server retention deletes the original rows.
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

Install this application update and additive batch migrations **031–032** using the
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

### Genuine SimPaths monitoring-cost comparison

This comparison uses **100,000 initial people**, the original prepared public
inputs and immutable runtime, original seed **606**, **one repetition per job**,
**2 vCPUs**, **4 GiB maximum heap** and a **5 GiB container**. There are no retries,
resource increases, adaptive CPU boosts or legacy profiling wrappers. Every mode
retains the ordinary production safety checks and output validation. The only
requested variation is optional review: off, external, or external plus Java.
The sampling interval is **15 seconds** in both enabled modes.

First run the scientific smoke below. It covers **2019–2021**, once per mode,
and checks the full execution path, including actual annual/population/heap/GC
observations in full mode. It is not an overhead estimate. Allow roughly
**20–40 minutes**, subject to actual initialization and host load. The one-hour
per-job cap is a failure bound, not a prediction of runtime.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python deploy/multirun/monitoring_cost_trials.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --scientific --population 100000 --end-year 2021 \
  --modes off external full --rounds 1 --sample-seconds 15 \
  --job-seconds 3600 \
  --output "/tmp-codex/monitoring-simpaths-smoke-$(date +%Y%m%d-%H%M%S)"
```

After the smoke passes, run the quiet comparison. It covers **2019–2035** for
**three counterbalanced rounds: nine separate simulations**. Round orders are
off/external/full, external/full/off, full/off/external. The earlier comparable
100,000-person, 2035 run took about 40 minutes, so budget **6–7 hours** for this
campaign. That estimate includes ordinary validation and setup; actual runtime
can differ. Each job has a two-hour cap, with the whole campaign separately bounded.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python deploy/multirun/monitoring_cost_trials.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --scientific --population 100000 --end-year 2035 \
  --modes off external full --rounds 3 --sample-seconds 15 \
  --job-seconds 7200 \
  --output "/tmp-codex/monitoring-simpaths-quiet-$(date +%Y%m%d-%H%M%S)"
```

Keep networking enabled for disposable test dependency installation. Run the
quiet comparison on mains power, without another benchmark or active laptop work.
Both campaigns require **15 GiB free disk** and **7 GiB available RAM**; the latter
includes 2 GiB for test services outside the model. Conditions run sequentially,
so the space requirement does not multiply by nine. The harness rechecks capacity
before each condition and removes that condition's bulky output after validation.
It preserves the original prepared package and runtime image. `--dry-run` performs
snapshot/plan checks without Docker or scientific execution, and creates no evidence
directory. The source snapshot is checked again before launches and final cleanup.

The test verifies the actual Docker image, command, CPU and RAM limits, fixed heap,
one successful attempt, released reservation and absence of resource changes.
It verifies Java's launch receipt and the presence/absence of observer files;
full mode must retain a complete initial-to-final sequence, all requested annual
years, population readings and the repetition boundary. Enabled scientific
conditions must record external samples. A silent fallback does not pass as full
monitoring. Source contracts and bounded diagnostics are retained, including for
failed conditions; completed measurements are saved before the next condition.

The readable report is `<output>/model-proof/comparison.html`; the detailed report,
bounded trace export, fixed scientific contract and per-attempt diagnostics are in
the same directory. Tables distinguish Docker lifetime from driver completion,
external sampled working memory from Java heap, and cumulative GC time from pauses.
Off mode has no optional memory sampler: its scientific lifetime CPU/peak RAM
remain **unknown**, rather than being measured by an added observer that would
change the baseline. The external collector's CPU excludes work in PostgreSQL and
the Docker daemon. Container/driver times still include their effects on completion.

Every job validates original settings, seed and all requested annual CSVs. CSV
checksums are compared with the first condition and saved before bulk removal.
Ross's [SimPaths issue #530](https://github.com/simpaths/SimPaths/issues/530)
describes the known shared-random-generator receipt-flag limitation:
`DonorTaxImputation.evaluate()` draws from `SimulationEngine.getRnd()` during
parallel benefit-unit labour calculations. A fixed seed does not fix which unit
receives each draw when evaluation order changes; legacy-benefit receipt also
depends on the UC flag. This is the leading explanation for isolated
`BenefitUnit.csv` differences, including between monitoring-off runs. Identical
inputs and seeds alone do not prove identical numeric results. Changed CSV hashes
remain explicit and make small timing differences **provisional**; the file hashes
alone cannot confirm that only the two reported flag columns changed. These are
independent one-repetition jobs, not the separate same-JVM memory-reclamation test.

**Host measurements still to do:** VPS mixed-load tests with the real quota broker.
The repeated fictional and genuine scientific quiet trials completed on 19 October
are recorded below; they cover the laptop and selected workloads.
Measure web/SingleRun responsiveness, chart delays, disk stalls and completion
distributions at different host loads. Observed timing differences do not establish
a causal overhead percentage or justify automatic tuning. Observations inform
reviewed profiles for new work.

## Scheduled admin exports and emails — implemented

The private transactional archive, hourly packing, daily queued multipart mail
and offline SQLite/HTML/CSV collation are implemented. See
[MONITORING_EXPORTS.md](MONITORING_EXPORTS.md) for configuration, live controls,
privacy, budgets, collection-failure behaviour, restore lineage and commands.

Migration **032** preserves observations and changed summaries in the same
transaction as their writes/pruning. Verified compressed database outbox parts
replace the pending journal; mail retries independently. A full journal pauses
optional collection with an explicit coverage warning while simulations continue.
The normal 2,000-row operator report remains a bounded view, not the archive.

Stable source/event IDs, revisions, numbered/checksummed parts and an offline
importer allow the administrator to collate emails over weeks or longer without
duplicating observations or repeatedly adding cumulative snapshots. Partial,
corrupt, conflicting and restored/forked history remains explicit. No raw model
rows, user addresses, input values, credentials or private paths are exported.

The first 82 native PostgreSQL checks passed, followed by the 69-check runtime
and online/offline recovery proof and a final 16-check export/mail follow-up.
The local review passed 47 frontend and 38 adapter/command/configuration checks;
these counts overlap. Evidence is recorded in the export guide. Actual SMTP delivery,
daily size, filesystem headroom and mixed-load overhead remain deployment checks.
Defaults keep this feature disabled until private operator configuration enables it.

### Provisional daily export sizes

A read-only measurement of the 19 October genuine monitoring trial's
`model-proof/measurements.json` found **1,596,321 bytes uncompressed** and
**157,958 bytes with gzip**: approximately **1.52 MiB / 154 KiB** for a limited
2,000-observation export plus nine attempt summaries. That file is explicitly
truncated, so it is not the whole six-hour campaign or a measured daily total.
The Java-only files from each full-mode 38-minute job were approximately 65 KiB,
compressing to about 7.2 KiB; complete exports also include external, host,
database, web and processing observations.

For planning at a 15-second interval, allow roughly **1–3 MiB compressed per day
with light/intermittent activity**, or **5–15 MiB with several continuously active
simulations and other service activity**. HTTP bucket counts, helper groups,
record contents and compression change these estimates; heavier use can exceed
them. Measure actual complete exports on the VM before fixing operational budgets.
The human-readable digest should be much smaller than the full numeric export.

Email attachment encoding adds approximately one third to the compressed payload.
The implemented **5 MiB compressed attachment budget per message** produces
roughly a 7 MiB email at that limit, before any substantial additional attachments.
Choose the final cap against the configured sender/recipient limits, and split
or independently preserve oversized exports without silently omitting records.
Volume estimates are provisional and predate the completed change journal;
measure its actual gzip volume, including changed cumulative snapshots, on the VM.

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
It verifies the harness, not a monitoring-overhead percentage.

The genuine scientific cost harness was strengthened on 19 October. **67 local
checks passed**, including production launch-switch routing, unchanged scientific
staging, CSV/seed/year validation, failed-diagnostic retention and real-JVM observer
tests. Both genuine command plans were checked against the original prepared
100,000-person package. The actual ordinary runner was staged and the observer
compiled against that package's immutable JAR without running a scientific model
or using Docker:
[/tmp-codex/monitoring-scientific-preflight-20261019-140504/report.json](/tmp-codex/monitoring-scientific-preflight-20261019-140504/report.json).
The native scientific smoke and longer quiet comparison are recorded below.
Local staging and functional checks alone do not establish scientific overhead.

The first native scientific smoke,
[/tmp-codex/monitoring-simpaths-smoke-20261019-141249/report.json](/tmp-codex/monitoring-simpaths-smoke-20261019-141249/report.json),
stopped after the off-mode simulation had completed successfully. Its retained
configuration/output receipts verify original seed 606 and 2019–2021, and both
reports confirm cleanup. The new harness incorrectly compared Docker's command
with only the adapter's three model arguments, omitting the production absolute-
deadline wrapper. This was a harness assertion failure, not heap exhaustion or
changed scientific settings; the original failed evidence remains unchanged.

The checker now requires the ordinary `/bin/sh` entrypoint, the exact deadline
wrapper and frozen deadline, the selected runner and the 4096 MiB heap argument.
Regression fixtures use the production Docker argument builder in all three
modes and reject changed deadlines, heaps, entrypoints and extra arguments.
The harness also retains an allowlisted actual launch record and container policy
before cleanup, without exporting Docker environment variables. **70 local checks
passed** after the correction. Production execution/model code and allocations
were not changed. All three scientific modes were rerun in the successful smoke
below; the first report remains a failed harness run.

### Genuine scientific monitoring smoke — 19 October

The corrected smoke passed in all three modes, with one successful attempt per
condition, fixed allocations, matching scientific launch contracts and cleanup
confirmed. It took **24.9 minutes** in total. Each job requested 100,000 people,
2019–2021, seed 606, one repetition, 2 vCPUs, a 4 GiB maximum heap and a 5 GiB
container, using the original prepared package and immutable runtime.

| Mode | Container lifetime | External review samples | Detailed Java records |
| --- | ---: | ---: | ---: |
| Optional monitoring off | 483.113 s | 0 | 0 |
| External review only | 458.012 s | 30 | 0 |
| External review plus Java | 456.442 s | 30 | 37 |

Full mode retained a complete sequence from initial setup to final completion,
all three annual years, population/heap/GC observations and the repetition boundary,
with no dropped Java records. Its Java log occupied 13,113 bytes. Neither disabled
mode generated Java observer records. External periodic sampling covers a sampled
window, not an exact lifetime memory peak; off-mode optional memory/CPU remain
unknown.

Only `BenefitUnit.csv` differed from the off-mode file in each enabled condition;
the other eight scientific CSVs matched byte for byte. This pattern is consistent
with the reported shared-RNG receipt-flag issue above. The retained file hashes
do not confirm which columns changed. This one-round functional smoke does not establish a
monitoring-overhead percentage or a speed improvement. The counterbalanced
2019–2035 quiet comparison below subsequently used the same artifacts and settings,
with the longer horizon and its corresponding fixed timeout.

Evidence: [model report](/tmp-codex/monitoring-simpaths-smoke-retry-20261019-143815/model-proof/report.json),
[readable comparison](/tmp-codex/monitoring-simpaths-smoke-retry-20261019-143815/model-proof/comparison.html)
and [harness cleanup report](/tmp-codex/monitoring-simpaths-smoke-retry-20261019-143815/report.json).

### Quiet genuine SimPaths monitoring-cost trials — 19 October

All **nine jobs passed**, each in one attempt, with cleanup confirmed at both model
and harness levels. The campaign took **5 hours 55 minutes**. Every job requested
100,000 people, 2019–2035, seed 606 and one repetition, using the same prepared
package, model JAR, immutable runtime and matched launch contract. Allocations
stayed at **2 vCPUs, 4 GiB maximum heap and 5 GiB container memory**, without
resource growth or retries. Enabled sampling used a **15-second interval**.

Times below are Docker container lifetimes, including initialization and output,
in **minutes:seconds**, rounded to the nearest second. The median and its difference
are calculated from unrounded seconds. Driver completion additionally includes
ordinary polling and output validation.

| Monitoring mode | Round 1 | Round 2 | Round 3 | Median | Range | Median difference from off |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Optional monitoring off | 37:10 | 39:13 | 39:02 | 39:02 | 37:10–39:13 | Reference |
| External review only | 39:01 | 38:50 | 36:50 | 38:50 | 36:50–39:01 | -0.51% |
| External review plus Java | 37:38 | 37:38 | 38:09 | 37:38 | 37:38–38:09 | -3.61% |

The mode order rotated between rounds: off/external/full, external/full/off,
full/off/external. There is no clear evidence of a monitoring slowdown in this
campaign. These negative differences do not establish a speed benefit or a zero
overhead guarantee: there are only three jobs per mode, run-to-run timing varies,
and scientific checksum equivalence is incomplete.

Eight CSVs, including `Person.csv`, matched byte for byte across all nine jobs.
Only `BenefitUnit.csv` differed, with nine different hashes but identical byte
counts. This also occurred between the three monitoring-off jobs. The pattern
strongly supports the known shared-RNG receipt-flag issue above as the likely
explanation. A read-only bytecode inspection on 19 October confirmed that the
actual frozen benchmark `model.jar` (SHA-256
`0c0396170793b6a66711e42e143f4cf2a3c9eb645dfed487bfa594bcced0108e`)
still calls `SimulationEngine.getRnd().nextDouble()` in `DonorTaxImputation` and
uses parallel streams in `LabourMarket`; this check rebuilt or ran no model.
The issue mechanism is taken from Ross's quoted report and checked against local
source and the frozen JAR; the GitHub page was unavailable during this review.
The campaign retained file hashes rather than the raw CSVs, so it cannot establish
that every changed cell is confined to `yBenUCReceivedFlag` and
`yBenLegacyReceivedFlag`. Detailed causal timing comparisons remain provisional;
the likely known cause does not invalidate the broader observation that no clear
monitoring slowdown was detected.

| Supporting measurement | External only | External plus Java |
| --- | ---: | ---: |
| External samples per job, range | 146–155 | 149–151 |
| Sampled working-memory peak per job, range | 3.56–3.66 GiB | 3.33–3.70 GiB |
| Sampled Java heap peak per job, range | Not measured | 2.79–3.15 GiB |
| External collector CPU during a job, median | 6.88 CPU seconds | 6.71 CPU seconds |
| Detailed Java records per job | 0 | 185, 185, 187 |
| Detailed Java files per job, range | None | 64.97–65.71 KiB |

All three full-mode Java logs retain complete initial-to-final sequences, all
17 requested annual years and the repetition boundary, with no dropped records.
The collector CPU figures describe roughly 0.3% of one CPU over each enabled job;
they exclude Java observer execution inside the model and work in PostgreSQL and
the Docker daemon, so they are not total monitoring-overhead estimates. Off-mode
optional memory and lifetime CPU counters remain unknown by design. Sampled
working memory is not an exact lifetime peak and cannot establish incremental
monitoring RAM without a measured off-mode reference.

The combined trace export reached its **2,000-observation bound** and is marked
truncated. All nine attempt summaries, per-run measurements, retained scientific
checksums and complete detailed Java files remain available separately. The
truncated combined timeline must not be treated as a complete campaign event log;
there is no need to rerun successful scientific jobs just to expand that view.

Use 15-second optional collection during the initial VM measurements, with the
agreed live external switch and new-launch Java switch. This test gives no reason
to increase each simulation's CPU or container allowance solely for monitoring.
The chosen VM still needs mixed simulation, Visualiser, SingleRun and database
load measurements; service-wide cost and responsiveness are not qualified by
sequential laptop jobs. Both optional controls retain their off defaults.

Evidence: [readable comparison](/tmp-codex/monitoring-simpaths-quiet-20261019-150929/model-proof/comparison.html),
[model report](/tmp-codex/monitoring-simpaths-quiet-20261019-150929/model-proof/report.json),
[bounded combined trace](/tmp-codex/monitoring-simpaths-quiet-20261019-150929/model-proof/measurements.json)
and [harness cleanup report](/tmp-codex/monitoring-simpaths-quiet-20261019-150929/report.json).

### Quiet fictional monitoring-cost trials — 19 October

Both counterbalanced campaigns completed successfully: three rounds each of off,
external-only and external-plus-Java review, 750,000 fixed-work iterations per job.
All 18 jobs had identical result fingerprints; all six Java-enabled jobs retained
complete initial-to-final numeric sequences without recorded loss. Cleanup passed.
The campaigns took approximately 19.2 minutes each, or 38.3 minutes combined.

| Mode | Median container time, 15-second interval | Difference from its off median | Median container time, 5-second interval | Difference from its off median |
| --- | ---: | ---: | ---: | ---: |
| Off | 120.195 s | Reference | 121.898 s | Reference |
| External only | 120.902 s | +0.59% | 122.197 s | +0.25% |
| External and Java | 120.100 s | -0.08% | 120.501 s | -1.15% |

| Supporting measurement (median per job) | 15-second interval | 5-second interval |
| --- | ---: | ---: |
| Reference container peak, off | 153.98 MiB | 154.15 MiB |
| Reference container peak, external and Java | 162.09 MiB | 161.70 MiB |
| Private collector thread CPU, off | 0.022 s | 0.020 s |
| Private collector thread CPU, external only | 0.316 s | 0.861 s |
| Private collector thread CPU, external and Java | 0.320 s | 0.820 s |
| Detailed Java files, external and Java | 2.94 KiB | 8.51 KiB |

The reference peaks are measured inside the workload container near JVM shutdown;
they exclude the host collector, PostgreSQL and Docker daemon. Collector CPU is
the collector thread's own CPU, not all monitoring-related CPU across the host.
One 15-second reading became unavailable immediately after its container finished;
the successful exit and complete Java sequence were retained. Missing native
readings were not replaced with zero measurements.

These three-run medians show no clear full-review slowdown on this fictional
workload. Negative differences do not demonstrate a speed improvement, nor do
these data prove zero overhead. Keep 15 seconds as the normal review interval;
five seconds supplies more detail for targeted investigation at greater collector
cost. The fixture has no scientific annual/repetition callbacks, large model heap,
quota broker or competing website load. Those costs still require representative
SimPaths and VPS measurements before recommending changed production allocations.

Evidence and readable tables:

- [15-second report](/tmp-codex/monitoring-cost-quiet-20261019-124321-15s/model-proof/report.json)
  and [tables](/tmp-codex/monitoring-cost-quiet-20261019-124321-15s/model-proof/comparison.html).
- [5-second report](/tmp-codex/monitoring-cost-quiet-20261019-124321-5s/model-proof/report.json)
  and [tables](/tmp-codex/monitoring-cost-quiet-20261019-124321-5s/model-proof/comparison.html).
