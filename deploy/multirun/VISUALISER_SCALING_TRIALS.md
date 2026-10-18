<!-- (C) Copyright 2026, by Ross Richardson

Local preparation scaling protocol: genuine raw outputs, fixed combined budgets,
summary-completion dependencies and bounded publication-backlog probes.
@author ross richardson
-->

# Visualiser preparation scaling

This benchmark asks two questions before implementing live resource handover:

1. Can two seed workers prepare **one configuration** sooner than a serial
   worker using the **same total 5 GiB and 2 vCPUs**?
2. For two completed configurations, how do one, two and four workers affect
   summary readiness, standalone charts and the finished baseline/alternative
   comparison? Does increased parallelism show diminishing returns or I/O
   pressure?

The first comparison partitions one configuration's repetitions, not its CSV
rows. Each seed uses an independent instance of the maintained Node parser.
No calculation source is changed. Correctness checks compare every seed summary
and publication/index/catalogue/view identity across resource profiles.

## Workload and resource profiles

The saved fixture has **two genuine repetitions each for a baseline and one
alternative**, 100,000 initial people and all 52 years, 2019–2070. Single profiles
use the baseline only; experiment profiles use both configurations. Original
seeds, raw-file hashes, calculation/dependency provenance and annual coverage
remain verified. No scientific simulations are run and no raw CSV is copied.

| Profile | Configurations | Concurrent workers | TOTAL container RAM | TOTAL vCPUs | Node old-space maximum per running task |
| --- | --- | --- | --- | --- | --- |
| `single-compact` | Baseline only | 1 | 2.5 GiB | 1 | 1.5 GiB |
| `single-serial` | Baseline only | 1 | 5 GiB | 2 | 4 GiB |
| `single-parallel` | Baseline only | 2 | 5 GiB | 2 | 1.5 GiB |
| `scaling-1` | Baseline and alternative | 1 | 2.5 GiB | 1 | 1.5 GiB |
| `scaling-2` | Baseline and alternative | 2 | 5 GiB | 2 | 1.5 GiB |
| `scaling-4` | Baseline and alternative | 4 | 10 GiB | 4 | 1.5 GiB |

Each case uses **one** Docker container. Its CPU/RAM limits cover the coordinator,
all Node processes, validation and publication helpers, and charged file cache.
There are no independently enforced per-worker cgroup limits inside this group.
Tasks share the total CPU quota, potentially unevenly. Node heap limits leave
headroom for native allocation and other processes; they are not whole-process
RAM limits. Parallelism never means assigning 5 GiB to each of two workers.

The pool schedules independent seed tasks, then:

- A configuration's standalone publication becomes eligible when both of its
  summaries are ready.
- The baseline/alternative publication becomes eligible when both configurations'
  summaries are ready, even if standalone publication is still pending.
- Eligible paired publication leads standalone publication, which leads another
  raw seed task. Existing tasks run to completion. The baseline's seed tasks are
  dispatched first, but both configurations can summarise concurrently.
- Each admitted publication continues through combination, validation, final
  publication, selected-chart indexing, catalogue and a representative chart
  read. There is no additional preparation queue between these stages.

This prototypes the agreed readiness rule; production database reservation
handover, baseline-first model admission and CPU lending remain separate work.
It uses the raw parser that is installed now; it is not an upstream parallel
parser patch or a claim that all preparation code has become multithreaded.

### Separate finished-publication backlog probe

After an experiment case is fully ready, four additional tasks regenerate the
**same** baseline/alternative publication from its retained summaries, with the
case's worker count and total quota. This is a short publication scaling probe.
Those four jobs are **not four different alternatives or independent
experiments**. They add no raw parsing or new scientific repetitions.

Their readiness times are reported separately from the genuine experiment's
elapsed time. Their checksums must equal the genuine pair's outputs. Use
`--publication-backlog 0` to omit this extra phase.

## Run on the quiet laptop

The existing `simpaths-visualiser-capacity:20261015` image is reused. New harness
files are snapshotted read-only into each run, so no new image build is required.
Keep other CPU/disk-heavy tests and software builds stopped. Docker credential
helpers may need networking even though the test containers have no network.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/visualiser_scaling_trials.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --fixture /tmp-codex/visualiser-raw-20261016-171532-fixture \
  --source-build /tmp-codex/visualiser-build-20261016-portable-b \
  --build /tmp-codex/visualiser-build-20261016-portable-b \
  --rounds 3 \
  --output "/tmp-codex/visualiser-scaling-$(date +%Y%m%d-%H%M%S)"
```

This runs 18 cases, rotating order over three rounds. Allow **roughly three to
four hours**, inferred from the previous raw-summary times; this is an estimate
for new parallel work. Each case has a one-hour timeout and the complete run a
six-hour timeout. For only the fixed-budget single-configuration question, add
`--profiles single-compact single-serial single-parallel` to the same command.

Preflight requires at least **8 GiB free disk** and the largest selected
container allowance **plus 2 GiB available host RAM**. The full selection therefore
needs 12 GiB available RAM before it starts and before the four-worker cases.
Check after closing other applications. A shortage stops the run with reports
preserved; it does not weaken the quotas. A shared lock prevents overlap with
other benchmarks using the same `/tmp-codex` evidence parent.

Containers run as the invoking user with no network, read-only root, no Linux
capabilities, no swap, bounded logs and only narrow read-only raw-source mounts.
Only benchmark-owned containers and scratch can be removed. Original sources,
earlier evidence, model images and Docker volumes are preserved. Scratch is
removed after each case only once its processes are confirmed stopped. Source
files are fully checksum-verified before and after the run, and before each
timed parse. Their filesystem cache is not deliberately flushed.

## Read the results

The output directory contains `comparison.html`, `comparison.csv` and
`report.json`, plus per-case/task reports, private logs and quarter-second native
resource samples. The HTML contains separate labelled tables for one-configuration
and two-configuration cases.

| Metric | Meaning |
| --- | --- |
| First configuration summaries | Time until both real seeds of the first ready configuration have verified, retained summaries. |
| All configuration summaries | Time until every tested configuration's seed summaries are ready. |
| First standalone charts | Time until the first configuration's standalone publication and chart catalogue are ready. |
| All required charts ready | Time until every tested standalone publication and the eligible genuine pair have ready catalogues. Single cases have no pair. |
| Genuine pair ready | Baseline/alternative comparison readiness from the common case start. |
| Extra backlog first/four pairs | Time from the separate replay phase start; excluded from genuine experiment readiness. |
| Group working/charged peak | Largest sampled value for the whole container during the main preparation phase; not per-worker usage. Charged memory includes file cache. |

Each publication records catalogue readiness and then verifies a representative
chart read. A failed validation, altered input, missing stage, sampler gap, OOM
or unconfirmed cleanup disqualifies that case. Outputs must match across all
passing profiles. Reports retain failure details; resource failures do not hide
the remaining profiles. An integrity failure stops further cases.

Three successful rounds use their median. Two use their arithmetic mean and are
marked incomplete; memory peaks remain the maximum observed. Tables do not call
an incomplete profile a qualified winner. Per-task stage snapshots overlap under
parallelism: do not sum their counters or attribute group memory to one worker.

The main group's CPU, I/O bytes, throttling and CPU/memory/I/O pressure counters
are retained. HTML details also show CPU throttling, CPU/I/O pressure and
extrapolated configurations per hour and per allocated vCPU-hour. These rates
include finished charts and describe only this finite workload; compare profiles
within the same scope, not as sustained production throughput.
Read/write contention can be investigated alongside timing, but
a slower parallel case alone does not prove a disk bottleneck. Cached reads
may not appear as physical disk reads. Quarter-second samples can miss short
spikes, so native memory/OOM counters remain part of acceptance.

This fixture is enough to test two seeds in parallel and four independent raw
seed tasks across two configurations. It does not establish scaling beyond four
tasks, ten genuine repetitions, a backlog of distinct policy comparisons or
performance alongside active simulations and HTTP requests. Those require
follow-up validation on the actual host before changing production defaults.

## Local verification — 18 October 2026

All 19 new scaling checks and 123 existing capacity/fixture/worker/comparison,
repetition, regeneration and raw-summary checks passed. The new integration
checks parse tiny fictional 52-year CSVs with the maintained calculation build,
retain genuine-shape seed summaries, then publish/index/read standalone and
paired charts. Serial and parallel paths produce identical data. They also
check cancellation, dependency timing, source corruption, sampler failures,
snapshot imports, group quotas and conservative native-container cleanup.

Logs: `/tmp-codex/visualiser-scaling-checks-final-20261018.log` and
`/tmp-codex/visualiser-scaling-regressions-20261018.log`.
Native CPU/RAM resource limits were represented by controlled cgroup files in
these local checks; this sandbox cannot access Docker. The command above is
run from the operator's terminal for real containment and performance
measurements; its completed native results are recorded below.

## Native results — 18 October 2026

Evidence: `/tmp-codex/visualiser-scaling-20261018-113148/report.json`, with labelled
tables in the same directory's `comparison.html` and `comparison.csv`.
All **18/18 cases passed**, three rounds per profile, in **3 h 23 min 54 s**.
Every container exited successfully; no OOM or sampling failures were recorded,
and container/scratch cleanup was confirmed. Source verification, real seed
coverage, raw-processing counts and calculated summary/publication identities
passed. A separate read-only review revalidated every saved worker report and
matching data identity against the snapshotted fixture.

Test image: `sha256:0bf66a27c52690e28674245bf7ceafc8b4e1eac704305abc1f54962e50a1837c`.
The whole run reused the same two genuine seeds per configuration; publication
backlog replays remain separate from genuine experiment readiness. All times
below are medians of three successful rounds, with source verification included.

### One configuration: retain the same model budget or release part of it?

| Workers | TOTAL RAM | TOTAL vCPUs | Both intermediate summaries ready (min) | Standalone charts ready (min) | Chart-ready range across rounds (s) |
| --- | --- | --- | --- | --- | --- |
| 1 | 2.5 GiB | 1 | 9.48 | 9.73 | 581.87–589.42 |
| 1 | 5 GiB | 2 | 7.59 | 7.84 | 465.54–476.07 |
| 2 | 5 GiB | 2 | 5.49 | 5.74 | 342.80–344.56 |

Within the same total 5 GiB / 2-vCPU allowance, processing the two seeds
concurrently reduced chart-ready time by **26.8%**, about 2.10 minutes. This is
the strongest direct evidence for retaining a completed model's full allowance
when two seed tasks are available. It also uses the allowance more fully:
median total CPU seconds increased from 565.75 to 666.76, so shorter elapsed
time is not a reduction in CPU work. Node old-space policy differs as specified
above: 4 GiB for the serial task versus 1.5 GiB per parallel task.

The compact case remains viable when capacity is scarce, but took 9.73 minutes.
Its comparison with the larger serial case changes both CPU and memory/heap;
it does not isolate the benefit of extra RAM alone. No one-worker outcome
establishes that adding a second worker within only 1 or 1.5 total vCPUs helps.

### Two configurations: standalone availability versus complete comparison

| Workers | TOTAL RAM | TOTAL vCPUs | First standalone charts (min) | Baseline/alternative comparison (min) | All required charts (min) |
| --- | --- | --- | --- | --- | --- |
| 1 | 2.5 GiB | 1 | 9.65 | 19.53 | 19.80 |
| 2 | 5 GiB | 2 | 5.82 | 11.83 | 11.83 |
| 4 | 10 GiB | 4 | 7.43 | 7.80 | 7.80 |

The **first standalone charts are one configuration's own results**, not a
completed baseline/alternative comparison. In the one-worker case the pair is
published before the alternative's separate standalone publication, explaining
the small difference between the final two columns.

Four workers cleared the complete workload **34.1% sooner** than two, using
twice the CPU/RAM allowance. The first standalone publication arrived **27.8%
later**, because all four seed tasks shared resources from the outset. Thus two
workers favour early standalone availability, while four favour the completed
comparison and clearing this whole backlog. Extrapolated main-phase completion
rates were 6.06, 10.14 and 15.39 configurations/hour respectively, but per
allocated vCPU-hour they decreased from 6.06 to 5.07 to 3.85. These are rates
for this finite preparation workload, not hosted simulation/service throughput.

### Memory and disk-pressure observations

These are the largest sampled **whole-group** values over the main phases of
all three rounds, not individual worker peaks or means:

| Profile | Group RAM limit (GiB) | Sampled working peak (GiB) | Sampled charged peak, including cache (GiB) |
| --- | --- | --- | --- |
| `single-compact` | 2.5 | 0.93 | 2.50 |
| `single-serial` | 5 | 1.68 | 5.00 |
| `single-parallel` | 5 | 1.56 | 5.00 |
| `scaling-1` | 2.5 | 1.01 | 2.50 |
| `scaling-2` | 5 | 1.57 | 5.00 |
| `scaling-4` | 10 | 2.80 | 8.21 |

The main experiment phases recorded median I/O pressure (`some`) of 1.23,
1.22 and 1.62 seconds respectively, over 19.80, 11.83 and 7.80 minutes.
Recorded memory pressure was below 0.11 seconds in every case. This does not
support disk waiting as the main explanation for diminishing returns **on this
laptop**. CPU work, CPU quota/sharing, processor frequency and cache effects
can also affect scaling; these measurements do not isolate their contributions.
VPS storage may behave differently. Modest working-memory estimates alone do
not qualify smaller limits: charged file cache approached several enforced
caps, and only the tested group allowances are verified here.

### Separate retained-publication backlog

| Workers | TOTAL RAM / vCPUs | First replayed pair ready (s) | All four replayed pairs ready (s) |
| --- | --- | --- | --- |
| 1 | 2.5 GiB / 1 | 33.12 | 132.86 |
| 2 | 5 GiB / 2 | 41.34 | 82.60 |
| 4 | 10 GiB / 4 | 53.04 | 55.85 |

Additional workers again cleared a backlog sooner while delaying its first
completed task. These are four regenerations of the same pair from retained
summaries, not four distinct alternatives. The genuine pair tasks in the main
phase took approximately 33–36 seconds each. Standalone tasks took roughly
15–20 seconds. Raw-to-intermediate processing remains the dominant cost here.

### Implementation implications

- Prototype evidence now supports two parallel seed tasks within a completed
  model's 5 GiB / 2-vCPU budget. The [production parallel integration](VISUALISER_PARALLEL.md)
  now supplies that worker lifecycle with real group admission and sequential
  fallback. Explicit atomic model-reservation handover remains separate work.
- Allow a larger ready-preparation backlog to use measured spare capacity:
  four workers helped this two-configuration workload. There is no evidence
  here for an arbitrary permanent ceiling of two, or for unlimited scaling.
- Preserve summary-completion dependency checks and continue eligible
  standalone/pair publication without another queue delay. Once only one useful
  task remains, consider returning unused capacity with verified accounting;
  live quota shrinking/transfer is not exercised by this benchmark.
- Validate scheduling alongside active models, PostgreSQL and HTTP/chart reads
  on the VPS before choosing the hosted admission limit. Ten genuine seeds,
  distinct multi-alternative backlogs and the one-vCPU/fractional-CPU parallel
  cases remain unmeasured. Production defaults are unchanged.

These successful sampling runs do not repair the two failed cases in the older
raw-summary matrix; their original evidence and unresolved trigger remain intact.
