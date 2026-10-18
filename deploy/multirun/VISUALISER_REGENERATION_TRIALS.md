<!-- (C) Copyright 2026, by Ross Richardson

Quiet-laptop resource matrix for retained-summary regeneration and worker concurrency.
@author ross richardson
-->

# Visualiser regeneration: RAM, CPU and worker count

This extends the [repetition-count benchmark](VISUALISER_REPETITION_TRIALS.md).
It prepares finished publications and indexed charts from existing intermediate
summaries. It does not parse raw CSVs, run simulations, use PostgreSQL, send
emails, or change production settings.

For the separate **raw CSV → intermediate summary** stage, see
[VISUALISER_SUMMARY_TRIALS.md](VISUALISER_SUMMARY_TRIALS.md). That matrix uses
only the two genuine repetitions per configuration, with the same RAM/CPU/worker
dimensions. Its two tasks summarise the baseline and alternative; the tasks here
each regenerate an entire baseline–alternative comparison.

## Test matrix

All combinations of the following are tested:

- **RAM per worker:** 2.5, 2 and 3 GiB, in that initial order.
- **CPU quota per worker:** 1 and 2 vCPUs.
- **Workers:** one or two.
- **Repetitions per configuration:** 2, 5 and 10.
- **Rounds:** three, with rotating profile/repetition order.

This is **12 resource profiles × 3 repetition counts × 3 rounds = 108 batches**.
Each batch prepares **two independent baseline–alternative comparisons**, so a
complete passing run performs 216 preparation tasks. This is not one experiment
with two alternatives. Both tasks use the same verified performance fixture, but
have distinct experiment identities and retained-summary files; one cannot reuse
the other's finished data or intermediates.

| Workers | CPU per worker | Total CPU quota | Execution of the two tasks |
| --- | --- | --- | --- |
| 1 | 1 vCPU | 1 vCPU | Sequential |
| 1 | 2 vCPUs | 2 vCPUs | Sequential |
| 2 | 1 vCPU | 2 vCPUs | Concurrent |
| 2 | 2 vCPUs | 4 vCPUs | Concurrent |

Every row is repeated for each RAM allowance. Two workers each with 3 GiB use
**6 GiB total container RAM**, for example. Comparing one worker with 2 vCPUs
against two workers with 1 vCPU each keeps the total CPU quota equal, but the
two-worker option reserves twice as much RAM. Both responsiveness and throughput
matter; the fastest batch is not necessarily the best use of a constrained VM.

## Measurement and validity

Each worker has its own Docker container with the exact native cgroup v2 CPU/RAM
quota, no swap, no networking, a read-only root filesystem and dropped
capabilities. The worker verifies those limits before and after processing. The
existing memory policy scales with container size:

| Container RAM | Node maximum old-space heap | Publication/index helper address-space limit |
| --- | --- | --- |
| 2 GiB | 1 GiB | 1.75 GiB |
| 2.5 GiB | 1.5 GiB | 2.25 GiB |
| 3 GiB | 2 GiB | 2.75 GiB |

The helper limit is a virtual address-space cap, not extra reserved RAM. These
trials test the complete existing memory policy, rather than reducing the Docker
limit while leaving an unsafe larger Node heap.

Fresh containers prime the selected summaries before timing begins. One worker
primes both independent tasks; two workers each prime their own task. After all
workers signal readiness, the host releases a common start marker. The marker is
mounted read-only in the containers. Native Linux monotonic timestamps measure:

- **First result:** barrier release to the earliest complete chart catalogue.
- **Both results:** barrier release to the latest complete chart catalogue.
- **Per-task stages:** summary restoration/checksums, statistical combination,
  validation, publication, indexing, catalogue and representative chart reading.
- **Resources:** quarter-second sampled working/total container memory, native
  cgroup peaks, CPU use/throttling, I/O counters, pressure and memory-limit events.

The representative chart read occurs after readiness. With one worker, that
read and scratch cleanup occur before the second task starts. Cache priming and
initial/final fixture verification are outside the first/both readiness timers.
Native whole-container memory peaks include priming; sampled per-task peaks can
miss short spikes. Filesystem caches are not forcibly flushed. This is not a
cold-disk trial.

Publication, catalogue, representative chart and scientific validation identities
must match for the same repetition count across both tasks, rounds and resource
profiles. These are synthetic performance repetitions, not additional independent
scientific runs or an uncertainty assessment.

## Running the full matrix

Use the existing `simpaths-visualiser-capacity:20261015` image. The new Python
workers and helpers are snapshotted and mounted read-only; **no image rebuild is
needed**. Run from the normal Docker-enabled terminal with the laptop otherwise
idle, outside other benchmarks:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/visualiser_regeneration_trials.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --fixture /tmp-codex/visualiser-capacity-fixture-20261015 \
  --source-build /tmp-codex/visualiser-build-20261010-selective-e \
  --build /tmp-codex/visualiser-build-20261016-portable-b \
  --memory-mib 2560 2048 3072 \
  --cpu-millis 1000 2000 \
  --workers 1 2 \
  --repetitions 2 5 10 \
  --rounds 3 \
  --output "/tmp-codex/visualiser-regeneration-matrix-$(date +%Y%m%d-%H%M%S)"
```

Allow several hours: the earlier one-worker 3-GiB/1-vCPU measurements took
31–47 seconds per preparation, and this matrix performs many more tasks plus
fresh priming for each batch. That is a planning estimate, not a guaranteed
duration. Defaults stop any one batch after **15 minutes**, including priming,
and the whole matrix after **eight hours**. The latter produces an incomplete
report if not all batches have been attempted. Both bounds can be set explicitly
with `--timeout-seconds` and `--overall-timeout-seconds`.

The largest case requires **8 GiB free disk and 8 GiB available host RAM**
(6 GiB containers plus 2 GiB host headroom). Preflight repeats before each batch;
the supervisor stops on less than 1 GiB filesystem headroom. Reports/source
snapshots accumulate but bulky generated summaries/publications/charts are
removed after each batch. Original fixtures/builds/images remain. Host Docker
credentials may need networking, so keep the laptop connected; the benchmark
containers have networking disabled.

A failed or out-of-memory worker is recorded and its peer is stopped; after
confirmed native container removal and scratch cleanup, the remaining batches
continue. A memory failure is an informative result, not a reason to skip other
RAM/CPU combinations. Timeout results are identified separately. Ctrl-C,
unconfirmed cleanup, filesystem shortage, invalid control/results or differing
scientific outputs stop the matrix. A completed matrix with failed batches exits
nonzero and reports `completed: true, passed: false`; interruption reports
`completed: false`.

The benchmark uses the same `/tmp-codex` research lock as the other standard
unattended commands. No unrelated containers, images or volumes are pruned.

## Reading the report

Open `comparison.html` in the output directory. Tables for 2, 5 and 10 repetitions
label per-worker and total CPU/RAM, passed rounds, first/both completion times,
variation, per-task stages and sampled memory. `comparison.csv` contains the
numeric summary. Each batch and worker has its own report/log; quarter-second
samples preserve CPU/disk/memory evidence. `report.json` records source/build/image
provenance and the full plan.

Hourly rates extrapolate the two-task batch, rather than measuring a whole hour
of production traffic. Comparisons per allocated vCPU-hour divide by the total
configured CPU quota; they are useful when CPU capacity is the bottleneck. Account
for RAM, interactive holdback and other protected service resources separately.
Failed/incomplete rows cannot be selected as performance winners, even when some
successful rounds have timings. Stage medians need not add up to the median
first/both completion times.

A single worker with the extra CPU may deliver one result sooner, while two
workers may finish both sooner. The tests measure that tradeoff; they do not
enable the second production worker, memory borrowing or CPU handover. Hosted
mixed simulation/preparation responsiveness still requires the VPS measurements.

## Regression coverage

Before the full native campaign, **77 focused local checks passed**, including
**16** new matrix/worker checks and the existing repetition/capacity suites.
The native measurements below were run from the user's ordinary terminal;
this session cannot access the host Docker socket.

Local checks cover the complete matrix and rotating orders, exact 2.5-GiB cgroup
validation, per-worker containment, two real processes sharing the host start
marker, first/both timing, output equality, OOM/peer cleanup, cancellation,
unconfirmed removal and continuation after a failed batch. Tiny genuine pinned
calculation/helper tests verify two independent summary-cache identities produce
matching charts with no raw parsing. The existing repetition and capacity suites
also check that their earlier paths continue to work.

## Completed native matrix — 17 October 2026

All **108 batches / 216 preparation tasks passed**, with cleanup confirmed, in
[the native report](/tmp-codex/visualiser-regeneration-matrix-20261017-174945/report.json).
The full campaign ran from 17:49:45 to 20:10:52 British Summer Time:
**2 hours 21 minutes 6 seconds**, including fresh cache priming and verification.
The [generated HTML tables](/tmp-codex/visualiser-regeneration-matrix-20261017-174945/comparison.html)
contain all 36 resource/repetition summaries with three rounds each.

### CPU and concurrency

For **ten repetitions per configuration**, with **3 GiB per worker**, median
times from the common start marker were:

| Workers | vCPU per worker | Total vCPU | Total RAM | First independent comparison ready | Both independent comparisons ready |
| --- | --- | --- | --- | --- | --- |
| 1 | 1 | 1 | 3 GiB | 46.6 s | 93.0 s |
| 1 | 2 | 2 | 3 GiB | 41.2 s | 83.1 s |
| 2 | 1 | 2 | 6 GiB | 53.5 s | 53.9 s |
| 2 | 2 | 4 | 6 GiB | 49.4 s | 49.6 s |

At the same **two-vCPU total quota**, two one-vCPU workers delivered both results
about **35% sooner**, corresponding to about **54% more comparisons per unit
time** for this batch, than one two-vCPU worker. The first result arrived about
12 seconds later. This is a latency/throughput tradeoff, and the two-worker option
uses twice the RAM. Across every RAM size and repetition count, two one-vCPU
workers beat one two-vCPU worker for completing both tasks: the extrapolated
throughput improvement ranged from about 47% to 60%.

Doubling a single worker's CPU quota reduced both-task time by about 6–13%
across the matrix. Doubling both concurrent workers' quotas reduced it by about
4–11%. Extra CPU can shorten an isolated wait when capacity is spare; these
gains do not justify assuming proportional acceleration or permanently doubling
the CPU reservation on a CPU-constrained host.

### RAM

At **1 vCPU per worker**, median time to finish both ten-repetition tasks was:

| RAM per worker | One worker, two tasks sequentially | Two workers, one task each concurrently |
| --- | --- | --- |
| 2 GiB | 105.2 s | 61.8 s |
| 2.5 GiB | 99.7 s | 58.5 s |
| 3 GiB | 93.0 s | 53.9 s |

The 2-GiB policy completed all tested pipelines, at about a 13–15% time penalty
relative to 3 GiB in these ten-repetition cases. The 2.5-GiB policy was about
7–9% slower. RAM savings can matter for VM-wide throughput even when individual
preparation is slightly slower. The memory and Node-heap limits changed together
as documented above, so this does not isolate the cause of the timing difference.

No cgroup OOM or OOM-kill events were recorded. Memory-ceiling contact/reclaim
events were recorded for the ten-repetition cases, including some 3-GiB cases;
container usage includes filesystem cache. Passing therefore does not imply that
the unused difference between sampled working memory and the limit is guaranteed
headroom for another process.

### Deployment implications

These results support testing a second **one-vCPU** worker for independent
pending regeneration tasks, rather than giving a single worker a permanent
second vCPU. Keep the protected worker's responsiveness, total RAM/CPU admission
and the difference between first-result and both-result times explicit. A second
worker does not divide one comparison pipeline into two parallel halves.

These are synthetic repetitions regenerated from retained summaries, with warm
filesystem conditions. They do not establish that 2 GiB suffices for raw CSV
summarisation, that two simultaneous raw parsers help, or that these timings hold
alongside simulations on a shared VPS. They do not enable production workers,
automatic resource loans or memory-growth recall. Those remain separate decisions
and mixed-load acceptance checks.
