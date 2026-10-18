<!-- (C) Copyright 2026, by Ross Richardson

Quiet-laptop measurement of genuine raw CSV to retained Visualiser summaries.
@author ross richardson
-->

# Raw-output intermediate-summary resource trials

This measures the expensive **raw CSV → intermediate statistics** stage separately
from the [finished-publication regeneration matrix](VISUALISER_REGENERATION_TRIALS.md).
Only the **two genuine repetitions** in each saved baseline/alternative are used.
There are no synthetic five/ten-repetition workloads, new simulations, database
changes, emails or production-setting changes.

## Work and resource matrix

Each batch has two tasks:

1. Summarise the baseline configuration's seeds **606 and 607**.
2. Summarise the alternative configuration's seeds **606 and 607**.

Both cover **100,000 initial people, 2019–2070 inclusive**. Each seed's Person and
BenefitUnit CSVs are parsed into the real, canonical per-seed statistics and
retained through the production `SummaryCache` and verified receipt code.
Baseline/alternative labels are attached later when comparisons are calculated;
both intermediate roles are canonically `baseline`, with their original seed.

One worker processes the two configurations sequentially. Two workers process
one configuration each concurrently, using **different raw files**. A worker
still processes its configuration's two seeds sequentially. This tests parallel
configuration summarisation, not parallel parsing of one configuration.

| Dimension | Values |
| --- | --- |
| Container RAM per worker | 2.5, 2 and 3 GiB |
| CPU quota per worker | 1 and 2 vCPUs |
| Workers | 1 and 2 |
| Genuine repetitions per configuration | **2 only** |
| Rounds | 3; resource-profile order rotates |

The full run is **12 resource profiles × 3 rounds = 36 batches**, comprising
72 configuration tasks and **144 genuine raw-to-summary calculations**. Those
are repeated calculations of the four saved runs, not additional independent
scientific runs. Every batch begins with an empty private intermediate cache.

Two workers reserve twice the RAM and CPU of one worker at the same settings.
Compare **one 2-vCPU worker with two 1-vCPU workers** for equal total CPU quota;
the latter still reserves twice the RAM. Node maximum old space follows the
existing container policy: **1, 1.5 or 2 GiB** for the 2, 2.5 or 3-GiB containers.
RAM/Node-heap effects therefore change together.

## Measurements and checks

After startup and metadata/build validation, workers wait for a common host
monotonic start marker. Measurements include:

- **First configuration ready:** time from release until either configuration's
  two per-seed intermediates are validated and retained.
- **Both configurations ready:** time from release until all four intermediates
  are validated and retained. Includes sequential waiting with one worker.
- Separate per-seed and per-configuration timings for **source checksum
  verification**, **raw parsing/statistical calculation**, and **summary
  validation/retention**. These are included in readiness times.
- Working and charged memory at **0.25-second intervals**, cgroup peaks and
  memory-limit/OOM events, CPU use/throttling, pressure and physical I/O counters.
- Logical input bytes, summary sizes, original seed identities, row/schema
  validation, all 52 years, and summary checksums/receipts.

Checksums must match **for each configuration and seed** across profiles/rounds.
The baseline and alternative are not expected to have the same statistics.
Every batch must show four raw computations, four misses, zero cache hits and
zero evictions across its workers. Full row data is not copied into the reports.

The original raw files are mounted **read-only**, only through their listed
retained-output directories. They are not copied or deleted. Initial/final host
checks verify the complete original inventory. The raw-fixture adapter records
both builds and checks unchanged scientific-source, dependency and toolchain
hashes before selecting the reviewed canonical-summary runner. Original fixtures
and build files remain untouched.

The intermediate cache is empty, but this is **not a cold-filesystem test**.
Checksumming reads a source before parsing it, and filesystem caches are not
forcibly flushed. Logical bytes need not equal physical disk reads. Final
publications, comparisons and charts are deliberately excluded from this test.

## Run quietly in the native terminal

Use the existing capacity image; **no image rebuild is needed**. The maintained
Python tools and selected build are snapshotted and mounted read-only. Keep
networking enabled if the host Docker credential helper requires it; the test
containers themselves have no network access.
Both build arguments below deliberately use `portable-b`: that is the original
build recorded by the retained raw fixture as well as the selected calculation.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/visualiser_summary_trials.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --fixture /tmp-codex/visualiser-raw-20261016-171532-fixture \
  --source-build /tmp-codex/visualiser-build-20261016-portable-b \
  --build /tmp-codex/visualiser-build-20261016-portable-b \
  --memory-mib 2560 2048 3072 \
  --cpu-millis 1000 2000 \
  --workers 1 2 \
  --rounds 3 \
  --output "/tmp-codex/visualiser-summary-matrix-$(date +%Y%m%d-%H%M%S)"
```

Plan approximately **8–10 hours** for the full three-round matrix, based on
earlier raw-processing measurements; this is an estimate. One round is roughly
one-third of the work. The defaults bound each batch to one hour and the full
run to twelve hours. This intentionally does not reuse the regeneration test's
15-minute batch timeout, which is too short for raw processing.

The largest profile requires **8 GiB free disk**, in addition to the raw files
already stored, and **8 GiB available RAM** including 2 GiB host headroom.
Preflight checks run before each batch; less than 1 GiB free during a batch stops
the run. Do not run alongside model/SingleRun/other resource benchmarks.

Reports include labelled **comparison.html**, **comparison.csv**, main/per-batch
JSON, per-worker/per-seed stage evidence, native samples and private logs.
Only generated intermediate scratch is removed after confirmed container exit.
A resource-failed batch remains visibly failed, with successful-round medians
labelled, while later profiles can run. Integrity failures or unconfirmed cleanup
stop further work. Keep the raw fixture/results until any planned repeat ends.

## Verification status

On 17 October 2026, **120 focused local checks passed**, including the 21 new
raw-summary checks and the existing capacity/regeneration regressions.

The local checks exercise the actual parser and production intermediate cache on
tiny fictional CSVs for all 52 years, source-corruption rejection, per-seed result
validation, exact resource arguments, isolated imports and native-controller
cleanup/failure handling. Mocked cgroup checks are correctness tests, not resource
measurements. Full sizing measurements require the native Docker command above;
the sandbox cannot access Docker's socket. VPS mixed-load acceptance and
production activation remain separate.

## Native results — 18 October 2026

Evidence: [original report](/tmp-codex/visualiser-summary-matrix-20261017-234536/report.json),
[labelled HTML tables](/tmp-codex/visualiser-summary-matrix-20261017-234536/comparison.html).
The original report and samples are preserved unchanged. Total elapsed time was
**8 h 26 min 48 s**. All 36 batches were attempted; **34 passed and two failed**.
Container cleanup was confirmed for every batch. The overall result correctly
remains **failed**, not an all-passing matrix.

### Failure investigation and remaining work

The failed batches were:

- `011-r1-2seeds-3072m-2w-1000cpu`: the baseline worker completed both
  intermediate summaries, then failed when stopping its resource sampler. The
  controller consequently stopped the still-running alternative worker. Its
  second seed's ready result was not recorded, so this is not a completed batch.
- `017-r2-2seeds-3072m-1w-2000cpu`: all four summaries completed with the same
  checksums as successful batches, but the resource sampler failed at shutdown.

Both affected samplers stopped their periodic background measurements partway
through processing, while explicit stage-boundary samples continued. The saved
worker reports contain `RuntimeError` / `metrics_error`, but the old collector
discarded the background exception's message. Its exact trigger therefore
cannot be established from this evidence. There were no native Docker OOM-kill
flags, and the available samples report no OOM event. A memory-allocation or
scientific-calculation failure is not demonstrated by these exceptions.

**Implemented diagnostic improvement:** the collector now records the precise
exception, stage and timestamp in `samples-errors.json` and in the raw worker's
report. Invalid counter errors identify the counter file/value. A final
available snapshot is retained for diagnosis, while any earlier sampling gap
still fails the worker. Repeated shutdown preserves the original error instead
of introducing a closed-file error. The original measurement validators and
allocation limits have not been relaxed.
On 18 October, all **123 focused local checks passed**, including tests that
preserve the exact background error, retain a diagnostic final counter snapshot,
keep a sampling gap as a failure, and preserve the cause through repeated cleanup.

**To do:** run the two focused checks below. If the sampler fails again, use its
new exact diagnostics to reproduce and correct the underlying cause. Passing
repeats would qualify those new measurements but would not explain the original
unrecorded error or rewrite the failed original report.

### Timing results

These are **medians of successful rounds**, in minutes. Each configuration has
two genuine seeds; both configurations means the baseline and alternative's
four intermediate summaries are ready. RAM and CPU are **per worker**.

| GiB per worker | Workers | vCPU per worker | Total GiB | Total vCPU | First configuration ready | Both configurations ready | Valid rounds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | 1 | 1 | 2 | 1 | 10.45 min | 20.08 min | 3/3 |
| 2 | 1 | 2 | 2 | 2 | 8.06 min | 16.23 min | 3/3 |
| 2 | 2 | 1 | 4 | 2 | 12.02 min | 12.76 min | 3/3 |
| 2 | 2 | 2 | 4 | 4 | 10.11 min | 10.43 min | 3/3 |
| 2.5 | 1 | 1 | 2.5 | 1 | 9.48 min | 18.72 min | 3/3 |
| 2.5 | 1 | 2 | 2.5 | 2 | 7.87 min | 15.75 min | 3/3 |
| 2.5 | 2 | 1 | 5 | 2 | 10.97 min | 11.05 min | 3/3 |
| 2.5 | 2 | 2 | 5 | 4 | 9.64 min | 9.73 min | 3/3 |
| 3 | 1 | 1 | 3 | 1 | 9.05 min | 18.11 min | 3/3 |
| 3 | 1 | 2 | 3 | 2 | 7.79 min | 15.51 min | **2/3; sampling failure** |
| 3 | 2 | 1 | 6 | 2 | 10.57 min | 10.65 min | **2/3; sampling failure** |
| 3 | 2 | 2 | 6 | 4 | 9.39 min | 9.44 min | 3/3 |

The two incomplete profiles' medians are provisional; the original HTML marks
them failed/incomplete. Their timings must not be described as three passing
rounds or used to choose a performance winner without that qualification.

### What this supports

- **Two workers help throughput.** Using the fully passing 2.5-GiB profiles and
  the same total two-vCPU allowance, two one-vCPU workers finished both
  configurations in **11.05 min**, versus **15.75 min** for one two-vCPU worker:
  **29.8% sooner / 42.5% greater batch throughput**. They require 5 GiB rather
  than 2.5 GiB of total RAM. The first configuration arrived about **3.10 min
  later**, so this does not improve every user's first-result wait.
- **Extra CPU helps modestly.** At 2.5 GiB, doubling a single worker's CPU quota
  reduced both-configuration time by **15.9%**; doubling both workers' quotas
  reduced it by **11.9%**. These improvements do not imply doubled throughput,
  and permanently taking those CPUs from simulations may reduce total service
  throughput.
- **2.5 GiB is a promising compromise.** At 1 vCPU per worker, it was about
  **3–4% slower than 3 GiB**, while saving 0.5 GiB per worker. The two-worker
  3-GiB comparison uses only two valid rounds. The 2-GiB profiles all passed,
  but were about **11% slower with one worker and 20% slower with two** than the
  corresponding 3-GiB timings. The Node heap and container limit change together.
- **Retaining summaries avoids the expensive stage.** CSV parsing/statistical
  calculation accounted for roughly **96–97%** of the timed stage total. Source
  verification took about **17 s per configuration** sequentially, or **21–22 s**
  with concurrent workers; validation/retention was about **2 s**. These are
  separate from finished comparison/chart generation measured by the earlier
  regeneration trials.
- **Intermediates remain compact.** All successful batches produced identical
  per-configuration/per-seed checksums. Four summaries occupied **121.93 MiB**,
  approximately **61 MiB per two-seed configuration**, against **13.60 GiB of
  source CSVs**. Each passed batch had four raw computations, no cache hits and
  no evictions.

Sampled working peaks across passing profiles were approximately **0.78–1.53
GiB per worker**. Charged memory reached the configured caps and reclamation
events occurred; these include filesystem-cache behaviour. None of the passing
workers recorded OOM or OOM-kill events. The lower working estimates do not prove
that smaller container caps are safe. The kernel documents the distinction
between max-boundary events, OOM and OOM-kill counters in its
[memory controller reference](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory).

These findings support comparing one/two workers with 2.5–3 GiB allowances on
the hosted mixed workload. They do not enable a second production worker,
simulation-to-summary resource handover, memory borrowing/recall, or establish
the optimal provider-wide allocation. Ten genuine repetitions and different
population/settings are not measured here.

### Focused follow-up commands

Only the two affected **3-GiB profiles** need another quiet check initially,
about **30 minutes total**, rather than the complete overnight matrix. This
creates new evidence directories and preserves the original failed report.
The selected build and genuine inputs remain the same; collector diagnostics
are newly snapshotted.

```bash
(
  set -e
  cd ~/git/SimPathsWeb/SimPaths
  export TMPDIR=/tmp-codex
  summary_retry_prefix="/tmp-codex/visualiser-summary-followup-$(date +%Y%m%d-%H%M%S)"

  for summary_retry_workers in 1 2; do
    summary_retry_cpu=2000
    if [ "$summary_retry_workers" -eq 2 ]; then summary_retry_cpu=1000; fi

    ~/simpaths-browser-tests/venv/bin/python \
      deploy/multirun/visualiser_summary_trials.py \
      --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
      --fixture /tmp-codex/visualiser-raw-20261016-171532-fixture \
      --source-build /tmp-codex/visualiser-build-20261016-portable-b \
      --build /tmp-codex/visualiser-build-20261016-portable-b \
      --memory-mib 3072 \
      --cpu-millis "$summary_retry_cpu" \
      --workers "$summary_retry_workers" \
      --rounds 1 \
      --output "${summary_retry_prefix}-${summary_retry_workers}w-${summary_retry_cpu}cpu"
  done
)
```
