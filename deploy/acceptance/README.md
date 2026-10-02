<!-- (C) Copyright 2026, by Ross Richardson
Real-model PostgreSQL SingleRun acceptance and historical state comparisons.
@author ross richardson
-->

# SingleRun browser acceptance

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
