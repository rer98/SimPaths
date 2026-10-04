<!-- (C) Copyright 2026, by Ross Richardson
Real-model PostgreSQL VM acceptance and historical SingleRun state comparisons.
@author ross richardson
-->

# SimPaths VM acceptance

The [deployment readiness checklist](../DEPLOYMENT_CHECKLIST.md) links the passing
real-model and local service rehearsals, states their scope and lists the remaining
host acceptance. This document describes the model/browser test commands;
[the VM runbook](../multirun/VM.md) and [backup runbook](../multirun/BACKUP.md)
describe the transport, mail, recovery, backup and application-update rehearsals.

## SingleRun browser acceptance

`run_two_session_acceptance.py` runs the same two-browser Quick Start 20,000
workflow with disposable PostgreSQL. It checks
concurrent Build and simulation execution, independent seeds, charts, downloads,
cross-owner denial, and preservation of the second session after Reset/Leave of
the first. It copies the candidate frontend into a private evidence directory;
it does not change the deployed frontend or rebuild model images.

Use the reviewed JAS-mine-web checkout, its VM dependencies, the dependencies in
`requirements.txt` in this directory, and installed Playwright Chromium. The
frontend needs the latest `requirements-vm.txt`, including Psycopg and
its connection pool. `postgres:17-alpine` must already exist. A validated 20k Quick Start image
must be present in the chosen catalogue. This runner does not pull images.

Run after other simulations finish, choosing a new output path:

```bash
python deploy/acceptance/run_two_session_acceptance.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --output /private/path/two-session-postgres
```

Supply `--catalogue` or `--image` when checking a reviewed candidate allocation.
The 20k test expects two 4 GiB containers, 2 CPUs each and 2 GiB heaps. It requires
at least 8 GiB available RAM and 6 GiB free root disk before starting; it stops if
observed disk capacity drops below 2 GiB. These thresholds came from the earlier
short 20k acceptance. They do not calibrate 50k model runtime or production host
capacity.

Each test uses a random model/deployment identity and isolated state configuration.
The runner creates a private temporary DSN file and does not start
Redis. Cleanup removes only labelled test resources and removes that DSN file.
Reports retain image IDs, functional checks, browser errors, resource samples and
status/chart/log response timings in milliseconds (mean, 95th percentile, maximum).
The timing comes from completed browser requests, including response transfer;
no response bodies or credentials are added to the timing records.

These are real-model browser checks with development networking. Repeat with realistic
MultiRun CPU/disk pressure and the native host's verified firewall/quota controls
before public deployment. The new common resource ledger requires explicit
`VM_SHARED_POOL_ID`/`VM_SHARED_BATCH_SCHEMA` settings and the same database;
without it, partition the services' budgets explicitly. This two-user proof
uses a standalone interactive registry; common admission has separate SQL tests.

## Real SingleRun and MultiRun mixed load

`run_mixed_load_acceptance.py` runs one real interactive Quick Start 20,000-person
simulation alongside one real 50,000-person MultiRun configuration, both for
2019–2026 with seed 606. SingleRun uses the normal frontend/browser routes;
MultiRun uses the normal PostgreSQL Queue, Worker, Docker executor and SimPaths
container adapter. The MultiRun submission in this proof uses the trusted service
API, rather than repeating the separately tested MultiRun form workflow.

Run after other simulations finish and their local launchers have stopped.
For SingleRun, save any output you need and use **Leave** before stopping the
launcher: ready model containers intentionally survive a frontend shutdown.

```bash
cd "$HOME/git/SimPathsWeb/SimPaths" &&
"$HOME/simpaths-browser-tests/venv/bin/python" deploy/acceptance/run_mixed_load_acceptance.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --prepared /tmp/codex-rer/multirun-prepared-50000-20260925 \
  --output "$HOME/simpaths-benchmarks/mixed-load-$(date +%Y%m%d-%H%M%S)"
```

The prepared path must contain a verified **public training** 50k Quick Start
receipt and its original immutable source image must already be installed.
The 20k catalogue image and `postgres:17-alpine` must also be installed. Use the
same VM/Playwright dependencies as the SingleRun acceptance and the model
requirements in `deploy/multirun/requirements.txt`. No images are pulled or built;
no email, real user inputs or persistent development database is used. The
existing source workbooks are not read to construct the simulations.

Starting checks require 11 GiB available RAM, 8 GiB free root storage, 6 GiB free
on the temporary-workspace filesystem, and 256 MiB on the evidence filesystem.
Other active JAS-mine sessions/batch containers cause the proof to stop before
launching anything. Close other applications if RAM is insufficient. The runner
keeps the reviewed allocations: SingleRun 2 CPUs / 4 GiB / 2 GiB Java heap;
MultiRun 2 CPUs / 5 GiB / 3 GiB heap. PostgreSQL has its own 768 MiB / 1 CPU
limit, outside the model pool. The temporary MultiRun copies are placed in the
system temporary directory; choose `--work-root /private/path` to use another
filesystem. Both root and workspace free space are checked during execution;
the proof stops and cleans up if either falls below 2 GiB.

The common pool has 4 CPUs, 9 GiB RAM and 20 GiB **logical** model storage, with
no additional interactive holdback in this admission test. The logical storage
allocations are not extra preallocated disk files or a host filesystem quota.
The physical free-space checks account for copies and headroom; production
disk estimates, enforced quotas and service/OS headroom still need a VM-specific
acceptance check. This proof deliberately gives the standalone interactive caps
more room, so its rejected extra launch has to come from the common pool.

The proof checks:

- One common PostgreSQL database and pool hold the exact interactive and batch
  CPU, memory and storage reservations, with real Docker limits and separate
  networks. An additional interactive launch is rejected and a different
  owner's additional batch job stays queued with zero attempts.
- At least 60 active-run chart responses before batch launch and 180 during
  real batch execution, plus normal status/log polling and populated charts.
  Docker samples must show CPU progress for both models in the same interval.
  Each running measurement waits for its outstanding requests and JSON checks
  before the test uses the browser's Pause control. Pause and recovery have
  separate labels, since Java's Pause write lock can temporarily block readers
  until the current simulation step finishes. The browser must settle to paused
  without an offline banner before recovery starts.
- A separate worker process stops and restarts while its model keeps running.
  Recovery must adopt the same attempt, execution key and container, and retain
  capacity throughout the expired-lease interval. The normal 60-second lease
  makes this phase take about a minute.
- The frontend stops and restarts with the same secrets and database; browser
  ownership, the interactive container and chosen parameters survive. The
  background configuration's inputs, settings and allocation remain unchanged.
- The interactive simulation completes all eight annual rows. Another browser
  owner cannot download them. Ordinary Reset preserves the saved output hash;
  Leave removes only the interactive container and allocation while MultiRun
  continues.
- MultiRun completes in one attempt, retains its frozen seed/settings/input
  identity, has all eight annual summary rows and complete Person/BenefitUnit
  years, and its actual retained output hashes match PostgreSQL completion
  records. The prepared source is verified again afterwards.

The private report contains checks, image/revision/input identities, exact
allocation snapshots, per-phase request means/medians/95th and 99th percentiles,
responses over 500 ms, resource samples, small summary CSVs, bounded model logs
and screenshots. Poll failures or missing timings in a measured running window,
uncaught browser exceptions, Java chart errors, sampling errors and uncertain
cleanup fail the report. Control/recovery polling is retained in
`other_phase_response_times` and the unfiltered `browser-timings.jsonl`;
its functional checks still require confirmed pause, recovery and isolation.
Cleanup checks ownership
and confirmed stop before deleting only this proof's resources. If model removal
is uncertain, the private workspace, PostgreSQL state and DSN file are retained
for recovery; inspect `recovery_files_retained` and the cleanup errors before
running another proof. Successful cleanup removes the temporary model copies.

This is a functional mixed-load check, with latency observations. The before and
during measurements come from different simulation years in one run, so they do
not establish a matched slowdown percentage or production capacity. It does not
accept production firewall/TLS/quota controls, preparation/aggregation/download
load, or resolve the separately recorded receipt-flag scientific RNG issue.

Local harness checks, without Docker or PostgreSQL:

```bash
python -m unittest discover -s deploy/acceptance -p test_mixed_load_acceptance.py -v
```

**Validation checkpoint (2–3 October):** 20 harness cases pass locally, including
four asynchronous cases for outstanding requests, JSON validation and failures
across measurement boundaries. The 28 reused completion/timing helper cases
passed during the original implementation. The first real attempt,
`mixed-load-20261002-210241/report.json`, stopped before provisioning because
7.21 GiB RAM was available, below the 11 GiB starting requirement. No models
or disposable database were started. That report contains no mixed-load model
measurements.

The subsequent `mixed-load-20261002-232140/report.json` passed all nine model,
admission, restart and isolation checks: both simulations completed all eight
years, MultiRun used one attempt, and final allocations were released. The final
polling check failed because two status timeouts and four aborted chart requests
during the test's direct Pause were still labelled `mixed-running`. The original
report remains failed. The corrected harness finishes the running measurement
before pausing through the UI and retains separate control summaries. No
application, authentication, secrets or cookie code changed for this correction.

**Full mixed-load proof passed — 3 October:**
`mixed-load-20261003-000304/report.json` passed all ten checks. Both real models
completed 2019–2026, MultiRun kept one attempt and its frozen inputs/seed, and
the final common ledger had no remaining allocations. Worker/frontend restart,
cross-owner download denial and Reset/Leave isolation passed. Resource sampling
and cleanup reported no errors, and the browser had no uncaught exceptions.

During mixed execution, all 944 measured requests succeeded, with none exceeding
the 500 ms polling interval:

| Endpoint | Requests | Mean (ms) | Median (ms) | 95th percentile (ms) | Maximum (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Status | 702 | 18.50 | 13.16 | 47.97 | 94.50 |
| Charts | 180 | 31.24 | 26.90 | 52.28 | 100.92 |
| Logs | 62 | 25.30 | 18.84 | 53.98 | 74.79 |

The 339 measured requests before batch launch also had no failures or responses
over 500 ms. The 179 resource samples showed a 7.49 GiB peak combined model
container memory reading (including cache), minimum available host RAM of
6.32 GiB and minimum free root storage of 15.09 GiB. Pause delays and requests interrupted by deliberate
page closure remain in the separate phase summaries and raw evidence. This
validates the tested pair; larger production workloads still need measurement.

For the generic database races, common admission, controlled cutover and HTTP proof,
see `JAS-mine-web/docs/vm-postgresql.md`. The current proof uses actual HTTP routes
with fictional Java output and busy MultiRun transactions, then a disposable
offline Redis export/import. It supplements the browser test. Redis is not a VM
runtime dependency. `run_browser_acceptance.py` and `run_vm_acceptance.py` also now
use PostgreSQL; Compose keeps a persistent volume and private credentials.

## Recorded laptop comparison

On 1 October, the Redis and PostgreSQL variants each passed all ten functional
checks using the same 20k image. Evidence is in
`two-session-redis-20261001-204419/report.json` and
`two-session-postgres-20261001-204658/report.json` under
`/home/rer/simpaths-benchmarks`. Both preserved the same surviving output hash and
reported no browser or Java chart-processing errors.

The PG run had higher polling latency, with occasional responses near 0.9 seconds.
JAS-mine-web subsequently removed repeated connection-configuration queries and
thread handoffs for checked in-memory reads. Its 360 frontend tests, 18 database
cases and HTTP comparison passed. The PostgreSQL model rerun also passed all ten
functional checks in `two-session-postgres-pooling-20261001-210059/report.json`,
preserved the same output hash and recorded no browser or Java chart-processing
errors. Mean status latency fell from 16.80 to 12.89 ms in the rerun, but occasional
slow chart/log responses remained. These short, separate runs establish functional
behavior, not equal throughput or latency. Keep the original Redis report as the
baseline. Full timings and remaining deployment checks are in the generic
`JAS-mine-web/docs/vm-postgresql.md` guide.

## Repeated state performance comparison

`run_state_performance.py` is retained for historical comparisons. It requires
an explicit `--redis-frontend` checkout of a reviewed pre-cutover JAS-mine-web
revision. The current frontend supports PostgreSQL only; use a separate legacy
checkout and the optional `requirements-vm-migration.txt` dependencies for this
benchmark. Do not point a legacy frontend at live PostgreSQL or stale Redis data.

The tool measures the real 50,000-person Quick Start with Redis and PostgreSQL
on the same machine. It runs three matched pairs by default,
alternating which backend starts each pair. Each trial uses a fresh state service,
frontend copy and model container, with the same reviewed image, parameters,
2019–2026 simulation and fixed seed 606. The model keeps its catalogue allocation:
5 GiB memory, 3 GiB Java heap and 2 CPUs. Only one model runs at a time. Both state
services have the same 768 MiB / 1 CPU limits.
Completion requires the engine to stop after the model's final-year cleanup,
scheduled at simulation time 2027 for a 2026 end year. JAS-mine's End event then
clears its event queue and resets the reported time to zero. The runner accepts
that reset only after observing execution in the final year, while the model
remains built. It also verifies that the saved HealthStatistics CSV contains
exactly one row for every requested year, 2019–2026. Java chart-processing errors
also fail the trial.

Run this after other simulations finish, using the same VM/browser dependencies
as the functional test. No image rebuild or pull is performed:

```bash
python deploy/acceptance/run_state_performance.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --redis-frontend /private/path/to/reviewed-pre-cutover-frontend \
  --output "$HOME/simpaths-benchmarks/singlerun-state-performance-$(date +%Y%m%d-%H%M%S)"
```

The runner requires 7 GiB available RAM and 8 GiB free root disk before each
trial. It stops when observed root space falls below 2 GiB. These are conservative
test headroom checks, not production capacity measurements. Allow time for six
Builds/full simulations and approximately 17 minutes of additional chart viewing.
The default execution limit is 30 minutes per simulation. `--pairs`,
`--chart-requests` and `--run-timeout` adjust the test; small sample targets are
useful for checking the harness but do not establish performance equivalence.

The report keeps these phases separate:

- **Build:** duration plus the browser's ordinary status/log polling.
- **Running:** full-speed simulation duration and naturally occurring chart,
  status and log responses. The simulation is allowed to finish without artificial
  pauses or slowdown to increase the sample count.
- **Completed charts:** at least 1,000 populated-chart responses per backend,
  across the trials, at the normal 500 ms interval. This test-only viewing loop
  calls the existing chart update function and waits for its Plotly drawing
  promises. It separately measures the complete update cycle. Ten warm-up
  responses per trial are excluded from these summaries.

Active-run chart counts are reported explicitly, with a flag indicating whether
they also reached the sample target. Completed-chart samples establish behavior
when viewing populated charts; they must not be treated as extra samples under
model CPU load. If the active sample is too small, additional full runs are needed.

The private report records averages, medians, 95th/99th percentiles, maxima,
failures and the percentage of responses exceeding 500 ms. It retains individual
timing samples and model resource samples, and checks that source fingerprints,
parameters and the downloaded HealthStatistics summary hash match across trials.
Response timings are joined to test-only server measurements:

- **State:** Redis commands/pipelines or PostgreSQL connection/transaction spans,
  including connection/pool waits. These are wall times, not pure query execution.
- **Java:** HTTP and bounded response-body handling in the existing proxy helper,
  excluding measured Redis credential lookups. Cache hits or shared requests may
  have no owned Java call.
- **Other:** the remaining server time. State-dispatch time is also recorded;
  it overlaps the state span and includes worker scheduling, so it must not be
  added to the other components.

Timing instrumentation lives only in the copied test frontend. It records fixed
route categories and durations, not URLs, response bodies, emails or credentials.
The real application continues to perform ownership and authorisation checks.
Cleanup removes only resources carrying the trial's own labels, and erases the
temporary PostgreSQL credential file. The last control status is recorded in the
report. The trial's private Java log is saved before removing its model, including
on failed execution trials.

A passing report means the measurements completed, sample targets were reached
and the checked settings/output matched. It does not automatically declare equal
performance. Compare each phase and the paired Build/run durations; operating
system cache and thermal state remain possible influences. This comparison runs
without additional MultiRun CPU/disk load. Mixed-load and production-host checks
remain separate.

At the original measurement checkpoint, 29 regression checks passed, including the real HTTP
routes with fictional state/Java, concurrent timing attribution, Redis pipelines,
private evidence, Start acknowledgements, final-year completion/clock reset,
complete annual summaries, failed-trial log retention and asynchronous Plotly
drawing. All 55 acceptance-helper tests passed. Both frontend HTTP-fixture tests
passed with the corrected command acknowledgements. The summary validator also
passed against an existing full-run CSV containing all eight requested years.

PostgreSQL-only implementation checkpoint, 2 October: 56 acceptance helper tests
pass locally. The corrected 15-case common-pool suite and HTTP/disposable-cutover
proofs passed in `postgres-queue-20261002-191628`. The two-browser model report
`two-session-postgres-cutover-20261002-191705` passed all ten functional checks,
but one resource sample raised an exception and failed its overall result.
That sampler discarded the error details. Sampling now records container removal
between inventory and stats as an expected Leave transition; other exceptions
are recorded by type/status and still fail acceptance. The next run
(`two-session-postgres-cutover-20261002-192637`) again passed all ten checks but
recorded `JSONDecodeError` during Reset/Leave. Immediate stats requests and sparse
inventory now avoid waiting for extra collection/inspection cycles. Interrupted
JSON is accepted only after rechecking that the container stopped or disappeared;
running/unknown states and failed rechecks still fail. All 56 helper tests pass,
including these transitions. The final
`two-session-postgres-cutover-20261002-193428/report.json` passed all ten checks
with no sampling, browser, Java chart or cleanup errors. It recorded 32 resource
samples and preserved the second model's output hash after Reset/Leave of the
first. Its 2,793 status responses averaged 10.77 ms (95th percentile 18.45 ms).
There were only 16 chart responses: mean 108.63 ms, slowest 1.09 seconds; the
slowest log response was 1.40 seconds. This checks functionality and isolation;
the six-trial 50k comparison below is the stronger performance evidence.
No live service has been migrated by this code update.

### Real-model results — 2 October 2026

All six trials passed in
`/home/rer/simpaths-benchmarks/singlerun-state-performance-20261002-093839/report.json`.
Redis/PostgreSQL each supplied more than 3,200 active-run chart responses and
1,002 completed-chart responses and drawing cycles. Active-run chart averages
were 16.89/17.85 ms; completed-chart medians were 9.68/11.02 ms; complete drawing
cycle medians were 64.8/66.0 ms. Every chart response and completed drawing cycle
remained below the normal 500 ms interval. No measured requests, browser/Java
chart checks, resource sampling or cleanup failed. Occasional Build log responses
above 500 ms occurred with both backends, primarily in Java/proxy handling.

Median Build times were 82.70/85.48 seconds; median full-run times were
552.38/551.07 seconds. All six trials had matching source/settings fingerprints
and the same HealthStatistics hash, with all eight requested annual rows.
That verifies the summary output, not every raw file. Temporary PostgreSQL
startup connection warnings recovered before Build. The report supports moving
ahead with PostgreSQL without an expected noticeable responsiveness loss for this
workload on the tested laptop. Multiple active models, real mixed MultiRun
CPU/disk load and production-host behavior remain separate checks. Detailed
phase/tail timings and cutover requirements are in
`JAS-mine-web/docs/vm-postgresql.md`, "Measured results — 2 October 2026".

### Earlier harness failures

The first real trial on 2 October completed Build in 82.17 seconds, then stopped
because the harness incorrectly expected `running` from the Start command. Java
acknowledges Start with `started`; subsequent status polls report `running` or
`paused`. The harness and fictional Java fixture now use those separate responses,
with regressions covering execution through final cleanup, early stopping and the
test time limit. `singlerun-state-performance-20261002-083737/report.json` records
the failed trial; it contains no completed performance comparison. Rerun the same
command above with a new output directory when repeating the benchmark; no model
image rebuild is needed.

The second real trial (`singlerun-state-performance-20261002-091309/report.json`)
built in 84.19 seconds and ran before the harness rejected a stopped engine.
Source inspection established that normal End processing resets the clock to
zero; the harness had incorrectly required a stopped clock of at least 2027.
That check is now corrected as described above. The old failed trial did not
retain the final status or Java log, so it cannot independently prove completion.
Neither failed trial provides the intended Redis/PostgreSQL comparison. Rerun the
same command with a new output directory when repeating the benchmark. Both
failures are excluded from the passing comparison above.
