<!-- (C) Copyright 2026, by Ross Richardson

Local retained-summary regeneration benchmark and interpretation of repetition scaling.
@author ross richardson
-->

# Regenerating finished Visualiser files from retained summaries

## What this measures

The focused laptop test compares **two, five and ten synthetic repetitions per
configuration**, with one baseline and one alternative. It therefore restores
four, ten and twenty intermediate summary files respectively. All 52 annual
years, 2019–2070, remain present. The same CPU and container RAM limits apply to
every trial. This is one worker preparing one comparison at a time.

The existing verified source summaries are relabelled into synthetic seed
identities and retained in a private instance of the actual summary cache. This
priming happens once, outside the measured trials. Each measured trial then:

1. Restores and verifies the selected summaries through the production cache.
2. Loads their statistical rows and combines matching seeds through the pinned
   calculation runner, including the paired calculations.
3. Validates annual coverage, repetition counts and nonzero policy differences.
4. Writes the finished publication with the real publication helper.
5. Creates the indexed chart sections and catalogue with the real chart helper.
6. Reads one representative chart after preparation completes.

The trial fails if any selected summary is missing or corrupt. It never falls
back to raw CSV processing. Publication, catalogue, chart and validation checksums
must match between rounds for the same repetition count. Different repetition
counts legitimately have different seed metadata and statistical sample counts.

**These are synthetic performance repetitions, not additional independent model
runs.** They cannot validate scientific uncertainty for ten genuine seeds. They
do exercise the extra summary loading and combining that increasing the seed
count requires. Dataset/category coverage affects the work and remains that of
the source summaries.

## Prepare and run

Use the existing capacity image `simpaths-visualiser-capacity:20261015`. The new
Python worker is snapshotted and mounted read-only, so the source change does
not require an image rebuild. If that image was removed, rebuild it first with
networking enabled:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
docker build \
  -f deploy/multirun/visualiser/Dockerfile.capacity \
  -t simpaths-visualiser-capacity:20261015 \
  deploy/multirun/visualiser
```

When the laptop is otherwise idle:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/visualiser_repetition_trials.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --fixture /tmp-codex/visualiser-capacity-fixture-20261015 \
  --source-build /tmp-codex/visualiser-build-20261010-selective-e \
  --build /tmp-codex/visualiser-build-20261016-portable-b \
  --repetitions 2 5 10 \
  --rounds 3 \
  --memory-mib 3072 \
  --cpu-millis 1000 \
  --output "/tmp-codex/visualiser-repetitions-$(date +%Y%m%d-%H%M%S)"
```

This makes nine measured comparisons, in orders `2, 5, 10`, then `5, 10, 2`, then
`10, 2, 5`. Use `--rounds 1` for a shorter first run. The default whole-benchmark
timeout is two hours; this is a stop limit, not a predicted duration.

The fixture's original build must match its provenance. Rebinding to the newer
build is allowed only when the recorded scientific source, dependency and
toolchain hashes match. It creates a new fixture snapshot and preserves the
original fixture/build. No raw outputs or private application state are mounted.

At the default 3-GiB container allowance, preflight requires **5 GiB available
RAM** (including host headroom) and **5 GiB free disk space**. The disk figure is
a safety allowance, not predicted retained report size. The supervisor stops
the worker if filesystem free space falls below 1 GiB. It shares the existing
research-benchmark lock, so do not run it alongside simulations or other capacity
trials. Scratch files and only this benchmark's container are removed; input
fixtures, builds and images are retained. No volumes or unrelated images are
pruned.

The existing local Docker credential helper may need host networking, so keeping
the laptop connected avoids the earlier authentication problem. The benchmark
container itself has networking disabled and needs no downloaded dependencies.
It runs as the current non-root user with fixed CPU/RAM limits, no swap, a
read-only root filesystem and dropped capabilities. Native cgroup v2 controls
are verified before processing.

## Reading the results

Open `comparison.html` in the new output directory. It contains labelled columns
for median time to ready, minimum/maximum times, all seven stages, publication
size and indexed-chart size. `trials.csv` contains each individual trial.
`report.json`, per-trial reports, bounded logs and quarter-second resource samples
retain the provenance and measurements.

Time to ready starts at entry to the regeneration pipeline and ends when the
catalogue is ready. It includes pipeline setup and restoration, combination,
validation, publication and indexing. The representative chart read is timed
afterward. Stage medians need not add up to the median total. Cache priming and
initial/final fixture verification are separate timings.

This isolates regeneration from **already retained summaries**. It excludes raw
CSV parsing and raw-source hash verification, scheduler waiting, HTTP permission
checks, browser/network latency and simultaneous simulations. The earlier
approximately one-minute warm result included additional source verification;
these focused timings are not directly interchangeable with it. Filesystem caches
are not forcibly flushed. The total cgroup memory peak includes priming;
per-stage sampled peaks can miss short spikes. Hosted mixed-load latency still
requires a VPS trial.

## Validation and storage-policy follow-up

The full-size local run on 17 October passed all nine trials with cleanup
confirmed in
[/tmp-codex/visualiser-repetitions-20261017-140408/report.json](/tmp-codex/visualiser-repetitions-20261017-140408/report.json).
With one worker, 1 vCPU and 3 GiB, median readiness times were 31.47, 36.87 and
46.63 seconds for 2, 5 and 10 repetitions respectively. Finished publication and
indexed-chart storage grew only from 259.71 to 261.09 MiB across those counts;
retained summaries grew from 125.09 to 625.44 MiB for the whole pair. These are
synthetic repetitions and warm filesystem measurements, not new model runs or
hosted sizing acceptance.

The next [RAM/CPU/worker matrix](VISUALISER_REGENERATION_TRIALS.md) tests 2, 2.5
and 3 GiB with one/two workers and 1/2 vCPUs per worker, retaining the same
repetition counts. The original single-worker command also accepts
`--memory-mib 2560` for an isolated 2.5-GiB run.

On 17 October 2026, **73 focused harness/regression checks passed** locally,
including the new repetition tests and existing capacity/worker/comparison
checks. Tiny real-helper tests cover all three repetition counts, repeat-output
identity, cache hits without raw processing and interruption cleanup. Mocked
Docker checks cover OOM, timeout, startup failure and unconfirmed removal. These
checks establish correctness, not native full-size resource measurements.

The agreed storage-pressure policy is recorded in `EXPERIMENT_ITERATION.md`.
The benchmark above does not enable eviction, automatic allowance expansion or
capacity-warning emails. Once those behaviours are implemented, their regression
proof should use tiny fictional files, a controlled clock and captured email
senders. It should cover active reads/downloads, persistent eviction markers,
authorised on-demand regeneration, notification deduplication, competing capacity
requests and disk space already promised to other service work. No 72-hour wait
or scientific simulations are needed for those correctness checks.
