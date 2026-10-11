<!-- (C) Copyright 2026, by Ross Richardson

Measured retained research output, scoped storage trial and repetition test plan.
@author ross richardson
-->

# Storage for the 100,000-person research workload

**First measurements: 10 October 2026. Two-repetition follow-up: 11 October 2026.**
Scope: public UK training inputs, 100,000 initial people, 2019–2070 inclusive
and the existing native CSV collectors. The baseline and a lower saving rate
(0.04) completed with seed 606; the baseline also completed a sequential
two-repetition test with seeds 606 and 607.

## What the completed runs establish

Each configuration retains about **3.46 GiB of output for one repetition**.
`Person.csv` and `BenefitUnit.csv` account for almost all of it; statistics and
options are small. The large native input copies were reclaimed after completion,
while `input/options.txt` remains. Repetitions produce separate annual CSVs, so
retained output grows with the number of repetitions even though subsequent
repetitions do not make another large input snapshot.

| Retained baseline file | Size | Share of retained output |
| --- | ---: | ---: |
| `Person.csv` | 2,697.0 MiB | 76.0% |
| `BenefitUnit.csv` | 784.0 MiB | 22.1% |
| `Household.csv` | 66.7 MiB | 1.9% |
| All other files together | 0.2 MiB | Less than 0.01% |

`Person.csv` and `BenefitUnit.csv` together occupy **98.11%**. The original
logical totals are **3,720,280,826 bytes** (baseline) and **3,719,603,337 bytes**
(alternative). At the audit, allocated blocks were 3.46482 and 3.46419 GiB respectively;
they depend on the filesystem. The projection uses the larger allocated value.

Encoded CSV record bytes by annual `time`, excluding the small file headers:

| Year | All nine baseline annual CSVs | Person CSV rows | Benefit-unit CSV rows |
| --- | ---: | ---: | ---: |
| 2019 | 48.2 MiB | 100,022 | 57,669 |
| 2020 | 56.6 MiB | 101,143 | 55,215 |
| 2026 | 61.7 MiB | 105,018 | 61,935 |
| 2050 | 70.9 MiB | 114,012 | 81,553 |
| 2070 | 76.1 MiB | 119,813 | 90,483 |

The alternative reaches 76.15 MiB in 2070. Later-year rows are larger/more numerous,
so multiplying an early year's storage by the horizon would underestimate the
measured total. Removing already-reclaimed input snapshots again would not reduce
the retained output; the annual microdata is the main storage cost.

The earlier **4 GiB fixed + 256 MiB per repetition** default was measured with
50,000 people through 2026. It is unchanged. It does not provide sufficient
incremental storage for this larger, much longer workload.

These measurements cover two configurations with one matched seed and two
sequential baseline repetitions, not ten repetitions, arbitrary policy settings,
larger populations or other collectors. The original one-seed sampled workspace
peaks were 5.84 GiB for both runs;
those include temporary inputs and working files. This read-only audit measures
retained bytes and current local filesystem allocation, not a new live peak.

## Scoped research storage allowance

Use **4 GiB fixed + 4 GiB per repetition** for a private research trial with
these inputs and collectors. The increment rounds the largest observed run plus
15% headroom up to a multiple of 256 MiB. The fixed allowance also uses the
existing input-size minimum, which includes the initial input copy, first native
snapshot, JAR and 1 GiB reserve. This is a measured candidate, not a new universal
default or a guarantee for untested configurations.

| Repetitions per configuration | Estimated retained output | Candidate workspace allowance | Free space required by the local proof |
| --- | ---: | ---: | ---: |
| 1 | 3.46 GiB | 8 GiB | 11 GiB |
| 2 | 6.93 GiB | 12 GiB | 15 GiB |
| 10 | 34.65 GiB | 44 GiB | 47 GiB |

Retained-output estimates multiply the largest completed repetition; they are
projections. Native CSV sizes can vary by seed and policy. The local proof adds
3 GiB for its services, environment and free-space reserve. It checks the actual
output filesystem before launching. An allowance reserves queue capacity; it
does not immediately consume that many physical disk bytes.

A baseline plus one alternative with ten repetitions each would retain about
**69.3 GiB** of raw output. Four users each retaining one such comparison would
account for about **277 GiB**, before prepared datasets, active input copies,
aggregate caches, download archives, database files or backups. User count does
not specify how many configurations are actively running. Admission reservations
and retained-output capacity must both fit the selected host.

Keep the tested **4 GiB maximum Java heap and initial 5 GiB container**, with the
existing controlled container growth ceiling of 7 GiB for this private trial.
The audit adds no new RAM measurement. Hosted concurrency remains to be tested.

## Reproduce the audit without simulations

The two original output directories were subsequently placed in lossless archives
to make room for the next trial. Restore them before rerunning the audit or the
original comparison commands; see the
[archive and restoration instructions](/tmp-codex/storage-cleanup-20261010/README.md).
All 26 archived files were decompressed and verified against their original
checksums before the uncompressed copies were removed. Native reports, manifests
and prepared inputs remain in place.

The audit verifies native completion and cleanup evidence, frozen inputs,
configuration/seed identity, all output hashes and every annual CSV. It reads
each CSV in one bounded streaming pass and reconciles the original encoded record
bytes by year, including quoted records. It copies no microdata, runs no model
and changes no release, dataset or service default. Evidence contains counts,
sizes, hashes and configuration metadata, not individual records.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/research_storage_audit.py \
  --prepared /tmp-codex/research-prepared-100000-20261009-232734/package \
  --completed /tmp-codex/research-100000-2070-20261009-232734 \
    /tmp-codex/research-alternative-100000-2070-20261010-092207 \
  --output "/tmp-codex/research-storage-audit-$(date +%Y%m%d-%H%M%S)"
```

Each new audit directory contains `report.json` with file/year byte counts,
`candidate-resources.json` with the existing resource-policy format, and
`test-plan.json` with the calculated allowance, frozen two-seed configuration,
command, runtime ceiling and disk preflight. The candidate policy is not
registered with the service. Use `--repetitions 10` to prepare a ten-seed plan.
`--completed` also accepts a later successfully completed repeated-run calibration;
each original seed and its annual files must be present.

The first audit passed in
[`research-storage-audit-20261010`](/tmp-codex/research-storage-audit-20261010/report.json),
including all nine annual CSVs for both original seeds, unchanged native manifests
and confirmed cleanup evidence. All **60** focused audit, retained-comparison and
calibration tests pass. The generated command's actual driver `--dry-run` matches
the recorded settings and deadline. At that check `/tmp-codex` had **10.67 GiB
free**, about **4.33 GiB short** of the two-repetition proof's 15 GiB requirement.
No simulation was started and no original output was removed.

The subsequent verified archiving recovered **5.18 GiB**, leaving **15.85 GiB
free** on the shared `/tmp` and `/tmp-codex` filesystem. This meets the disk
preflight for the two-repetition trial, which subsequently passed on 11 October.
Restoring the earlier outputs would consume that recovered space. The
[cleanup report](/tmp-codex/storage-cleanup-20261010/report.json) records the
archive checksums and recovered space.

## Two-repetition measurement

Start with two repetitions in one native configuration, seeds **606 and 607**,
using the same prepared inputs. This tests sequential output growth and input
snapshot reuse. It retains the existing annual/output/hash/options, ownership,
status-request and confirmed-cleanup checks. One attempt keeps the measurement
from mixing several executions. The private driver monitors working storage;
this run does not establish kernel quota enforcement on the eventual host.
The existing calibration driver accepts a total allowance: the generated command
passes **12,288 MiB**, calculated by the normal resource-policy storage function.
The per-repetition candidate and its workload scope stay in the audit/test plan;
this does not introduce automatic resource selection in the normal service.

The command below previews the plan and starts no services or simulation:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/research_calibration.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --prepared /tmp-codex/research-prepared-100000-20261009-232734/package \
  --population 100000 --end-year 2070 --repetitions 2 \
  --heap-mib 4096 --memory-mib 5120 --max-memory-mib 7168 \
  --storage-mib 12288 --setup-seconds 900 --repetition-seconds 14400 \
  --max-attempts 1 \
  --output "/tmp-codex/research-two-seed-$(date +%Y%m%d-%H%M%S)" \
  --dry-run
```

After the filesystem has **at least 15 GiB free** and the machine has **9 GiB
available RAM**, stop local simulation launchers and remove `--dry-run` to execute.
The driver rechecks both conditions and reuses the verified preparation. It
retains the new verified output by moving it within the same filesystem, without
duplicating it. The earlier baseline/alternative outputs are preserved in their
verified archives.

The two original containers lasted approximately 92 and 99 minutes. Two new
repetitions may therefore take about three to four hours; this is an estimate,
not a runtime guarantee. The trial allows 15 minutes setup plus four hours per
repetition, with one attempt. Do not infer ten-seed or four-user throughput from
this local measurement.

### Completed two-repetition test — 11 October 2026

Both native repetitions completed all **52 annual years** with their original
seeds **606 and 607**, settings and verified output hashes. The
[native model report](/tmp-codex/research-storage-audit-20261010-two-seed-trial/model-proof/report.json)
and [driver report](/tmp-codex/research-storage-audit-20261010-two-seed-trial/report.json)
both record success and confirmed cleanup. The driver also passed its worker and
resource-recovery checks against disposable PostgreSQL: **38 tests**, without
skips. The subsequent
[read-only audit](/tmp-codex/research-storage-audit-20261011-two-seed/report.json)
verified all **25 retained files**, including every annual year in **18 CSVs**
(nine per seed), against their original manifests in 151 seconds. Its
[verification record](/tmp-codex/research-storage-audit-20261011-two-seed/verification.json)
binds the new audit to the unchanged native reports and output manifest.

| Measurement | Result |
| --- | ---: |
| Retained output, both repetitions | 6.93 GiB (7,440,879,720 bytes) |
| Retained output per repetition | About 3.46 GiB |
| Sampled workspace peak | 9.30 GiB |
| Workspace allowance | 12 GiB |
| Headroom above the sampled workspace peak | 2.70 GiB |
| Sampled working-RAM peak | 4.39 GiB |
| Sampled Java heap peak | 3.66 GiB |
| Container lifetime, including staging and initialisation | 3 hours 9 minutes |
| Proof elapsed time, including output verification | 3 hours 21 minutes |

The first repetition made one 608,042,616-byte native input snapshot; the second
made **no additional input copy**. All large input copies were reclaimed after
completion, while both `options.txt` files and scientific outputs remain. The
second seed added about **3.47 GiB**, supporting the 4 GiB-per-repetition research
allowance for this tested workload. This is separate from the existing short-run
256 MiB increment.

The memory recovery controller increased the container from **5 to 6 GiB** during
the second repetition. Both seeds finished in the same original attempt and JVM;
the maximum Java heap remained **4 GiB**, and no retry was used. The increase is
evidence that live resource growth works for this workload; it does not establish
that a fixed 5 GiB container would have failed. Working RAM excludes inactive
file cache and must not be substituted for the container's full memory allowance
when planning host capacity. Four-user capacity must account for possible growth.

All **1,097** measured owner status requests returned 200, with median **67.7 ms**,
p95 **83.8 ms** and maximum **116.7 ms**. No browser errors were recorded. The
model container, disposable database and capacity reservations were released.
Workspace and JVM values are sampled measurements; this trial used application
storage monitoring and does not establish native quota enforcement on the host.

The updated unexecuted ten-seed plan still uses **44 GiB workspace allowance**
and requires **47 GiB free** for the local proof. It projects about **34.65 GiB
retained output** per configuration from the largest measured repetition. This
is a projection from two seeds; it does not establish ten-seed capacity or runtime.

## Fixed memory benchmarks — 11 October 2026

Case A repeated the same baseline with **100,000 people, 52 annual years and
seeds 606 and 607**, using two CPUs, a **4 GiB maximum heap** and a **fixed
5 GiB container**. Initial and maximum container allocations were equal;
the heap ceiling stayed at its starting value, storage growth was prevented
and only one attempt was permitted. Monitoring remained enabled. Both the
[model report](/tmp-codex/research-case-a-fixed-5g-20261011/model-proof/report.json)
and [driver report](/tmp-codex/research-case-a-fixed-5g-20261011/report.json)
record success, all original annual outputs and confirmed cleanup.

| Measurement | Earlier run with live growth | Case A, fixed memory |
| --- | ---: | ---: |
| Maximum Java heap | 4 GiB | 4 GiB |
| Observed container allowances | 5 then 6 GiB | 5 GiB throughout |
| Sampled working-RAM peak | 4.39 GiB | 4.38 GiB |
| Sampled heap peak | 3.66 GiB | 3.74 GiB |
| Container lifetime | 3h 9m 30s | 3h 31m 13s |
| Garbage-collection counter time | 5m 23s | 5m 58s |
| GC counter time / measured JVM execution | 2.9% | 2.8% |
| Retained output | 6.93 GiB | 6.93 GiB |
| Sampled workspace peak | 9.30 GiB | 9.30 GiB |

Case A establishes that the tested two-seed configuration **can complete within
a fixed 5 GiB container**. The earlier live increase was a pressure-triggered
safeguard, not proof that 5 GiB would fail. Total container memory including file
cache reached approximately the hard limit; working RAM excludes inactive file
cache and is not a measure of freely available container or host capacity.

Case A's container lifetime was approximately **11.5% longer** than the earlier
run. A single laptop comparison cannot attribute this to memory pressure or
establish a dependable speed difference. GC counters increased by about
35 seconds; they do not explain the entire 22-minute runtime difference and
must not be interpreted as a measured saving available from a larger heap.
Container lifetime includes staging and initialisation as well as execution.

All **1,222** measured status requests returned 200: median **71.1 ms**, p95
**104.1 ms**, maximum **182.7 ms**. There were no browser errors. The complete
proof, including output validation, took **3h 42m 49s**. Scientific output hashes
are compared for matching seeds: **eight of nine annual CSVs match for each
seed** against the earlier two-seed run. `BenefitUnit.csv` differs at equal byte
size, consistent with the existing reproducibility limitation. File equality is
recorded explicitly rather than assumed for every native output.

Retain native reports, per-file hashes and sizes, per-year counts and frozen
settings before deleting bulk benchmark output. The original completion reports
remain historical evidence; a separate disposal receipt records removed output.
Deleting microdata prevents rerunning aggregation checks on it, but does not
remove the recorded memory, runtime and storage measurements.

The [Case A output audit](/tmp-codex/research-case-a-fixed-5g-audit-20261011/report.json)
verified every original file hash and all annual counts. Following a further
unchanged-file check, all **25 bulk output files** were removed. The
[compact evidence](/tmp-codex/research-case-a-disposal-20261011/compact-evidence.json)
retains these statistics, frozen identities, timings and same-seed comparisons;
the [disposal report](/tmp-codex/research-case-a-disposal-20261011/disposal-report.json)
confirms unchanged native reports/manifests and prepared inputs. This leaves
**18.60 GiB free** on `/tmp-codex`, sufficient for the next trial's disk preflight.

The remaining sequential benchmarks are:

| Case | Maximum Java heap | Fixed container memory | Purpose |
| --- | ---: | ---: | --- |
| B | 4 GiB | 6 GiB | Measure the larger container without changing the heap |
| C | 5 GiB | 6 GiB | Measure a 25% larger heap within the same container |

Case C's 5 GiB maximum heap was agreed on 11 October. The difference between
container and maximum heap is **1 GiB**, matching Case A; Case B leaves 2 GiB.
These are differences between configured limits, not measurements of native
memory use. Compare B/C runtime, GC and actual working RAM together; increasing
the maximum heap can also increase actual memory usage.

Reuse the pinned preparation and seeds, two CPUs and 12 GiB storage allowance.
Set equal initial/maximum container memory and one attempt, preventing expansion
and resource retries during the measurements. Case B/C require **15 GiB free
disk** and **8 GiB available RAM** under the existing proof preflight. These
tests do not change production defaults or disable production recovery.
Final starting profiles await the remaining cases and hosted workload tests.
An earlier check found **7.2 GiB RAM available**, below Case B's 8 GiB preflight
requirement. The test-service reserve was not reduced. Case B has now been
started from the native terminal; its result remains pending. Case C has not
started. After B finishes, preserve its file hashes, annual counts and resource
measurements, reclaim bulk output and recheck free space before executing C:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python deploy/multirun/research_calibration.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --prepared /tmp-codex/research-prepared-100000-20261009-232734/package \
  --population 100000 --end-year 2070 --repetitions 2 \
  --heap-mib 5120 --memory-mib 6144 --max-memory-mib 6144 \
  --storage-mib 12288 --setup-seconds 900 --repetition-seconds 14400 \
  --max-attempts 1 \
  --output "/tmp-codex/research-case-c-fixed-6g-heap-5g-$(date +%Y%m%d-%H%M%S)"
```

### A/B/C comparison record

Use the same release, prepared inputs, collectors, two-CPU allowance, seeds
606/607 and two repetitions through 2070. Record test dates and competing laptop
activity. A/B changes only the container ceiling; B/C changes only the heap
ceiling. Repeat a case if the observed difference is too small or variable to
support a decision. Keep production safeguards separate from these fixed tests.

| Measurement | A: 4 GiB heap / 5 GiB container | B: 4 GiB heap / 6 GiB container | C: 5 GiB heap / 6 GiB container |
| --- | --- | --- | --- |
| Status | Passed | Running; result pending | Not started |
| Container lifetime | 3h 31m 13s | Pending | Pending |
| Last sampled JVM elapsed time | 3h 30m 31s | Pending | Pending |
| GC counter time | 5m 58s (2.8% of last sampled JVM elapsed time) | Pending | Pending |
| Sampled working RAM / heap peak | 4.38 / 3.74 GiB | Pending | Pending |
| Total cgroup memory / pressure | Approximately container ceiling; retain native readings | Pending | Pending |
| Sampled workspace peak / retained output | 9.30 / 6.93 GiB | Pending | Pending |
| Attempts / growth | One / none | Verify | Verify |
| Status latency p95 / maximum | 104.1 / 182.7 ms | Pending | Pending |
| Annual output / seed/settings / hashes | Verified; known BenefitUnit differences recorded | Pending | Pending |
| Settled cleanup / compact evidence | Confirmed and archived | Pending | Pending |

Use native reports for exact values rather than deriving them from terminal
sampling lines. Preserve B's evidence and compare hashes before removing its
bulk output to make room for C. The command above is ready but has not been run.
The telemetry elapsed value is the final sample's JVM timer, not an independent
measurement of the complete simulation phase. These single trials can identify
feasible allocations and suggest follow-up timing tests; they cannot establish
a dependable speed benefit from a larger container or heap. Keep BenefitUnit
mismatches visible, and align annual output by seed rather than timestamped folder.

### Automated comparison, archival and disposal

`research_benchmarks.py` uses only the Python standard library. Its `compare`
command reads bounded completion metadata or compact archives, not the bulk CSVs
or a running trial's progress log. Missing/in-progress cases stay pending or
incomplete; failed/unconfirmed-cleanup cases are reported without timing claims.
It rejects changes to model/image, prepared inputs, seeds, collectors, calibration
driver/monitor or other frozen settings beyond A/B/C's intended memory changes.
Annual CSVs are aligned by seed and filename, ignoring timestamped folder names.

Equal-size BenefitUnit hash differences are explicit unresolved reproducibility
warnings. BenefitUnit size changes and mismatches in other annual CSVs are
unexpected mismatches; the command writes the evidence and exits nonzero.
It does not certify scientific equivalence or select a production resource profile.
GC percentages use the last sampled JVM elapsed timer. Status percentiles use
nearest rank with counts, and exclude unsuccessful requests from the successful
latency distribution while counting those responses separately.

An unchanged-allocation `waiting` pressure notice does not mean growth occurred.
Case A has one such notice. The comparator accepts it only at the exact original
two-CPU/RAM/storage allocation; confirmed growth, changed observed ceilings and
resource retries make a trial ineligible for this fixed-memory comparison.

The existing Case A audit has been preserved in a portable compact archive:
[`case-a-archive`](/tmp-codex/research-benchmark-tool-20261011/case-a-archive/archive.json).
The [initial generated comparison](/tmp-codex/research-benchmark-tool-20261011/comparison-final/comparison.md)
contains Case A and marks B incomplete and C pending. It performs no new model
execution or bulk-output read/deletion. To create a fresh comparison:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
python3 -m deploy.multirun.research_benchmarks compare \
  --case A=/tmp-codex/research-benchmark-tool-20261011/case-a-archive \
  --case B=/tmp-codex/research-case-b-fixed-6g-20261011 \
  --output "/tmp-codex/research-benchmark-comparison-$(date +%Y%m%d-%H%M%S)"
```

Once C completes, add `--case C=` followed by its actual completed evidence
directory. Preserve each native report as historical evidence. Comparison reads
their recorded hashes; it does not attest to the current state of bulk files.

After B completes with confirmed cleanup, run the existing read-only
`research_storage_audit.py` on B with the pinned preparation before reclaiming
space. This full audit reads the original CSVs and verifies all file hashes and
annual counts. Then use these separate, explicitly requested operations, selecting
the real successful audit and a new archive directory:

```bash
python3 -m deploy.multirun.research_benchmarks archive \
  --completed /tmp-codex/research-case-b-fixed-6g-20261011 \
  --audit /path/to/case-b-storage-audit/report.json \
  --output /tmp-codex/research-case-b-compact-archive
```

Archival copies only the small completion records and audit statistics. It binds
them to the original reports/manifests and stores no scientific rows. It records
a historical audit, not a fresh output hash check. Review the archive and comparison
before invoking disposal:

```bash
python3 -m deploy.multirun.research_benchmarks dispose \
  --archive /tmp-codex/research-case-b-compact-archive \
  --completed /tmp-codex/research-case-b-fixed-6g-20261011
```

Disposal requires the same successful completion and unchanged evidence, hashes
every remaining bulk file before deleting the first, and rechecks file identities
immediately before unlinking. Symlinks, extra/missing files without its journal,
changed files and concurrent disposal are rejected. A durable per-file journal
allows rerunning the identical command after interruption; an unexpected change
still requires investigation. It removes only manifest-listed output files,
preserving directories, native reports, prepared inputs and other runs. Byte
counts are logical bytes removed, not a promise of free-space gain from shared
blocks, hard links or open files. Recheck physical free space before launching C.

The comparison/archival/disposal tests use tiny fictional files and no Docker,
database, models or emails. The focused command is:

```bash
TMPDIR=/tmp-codex python3 -m unittest deploy.multirun.test_research_benchmarks -v
```

Validated on 11 October: all **21 checks pass**, including metadata/file changes,
running/failed trials, symlinks, exclusive disposal locking and restart after an
interruption between unlink and journal update. Native Case A's archive reads
successfully and its original completion records remain unchanged. See the
[verification receipt](/tmp-codex/research-benchmark-tool-20261011/verification.json)
and [test log](/tmp-codex/research-benchmark-tool-20261011/tests-complete.log).
No real bulk-output disposal has been performed by the new tool.

## Remaining host checks

Follow the [hosted load-test plan](../HOSTED_LOAD_TEST_PLAN.md) for sequential
capacity stages, four-user workflows, provisional responsiveness targets and
shared-versus-dedicated processor decisions.

1. On the selected host, measure ten repetitions, different permitted policy
   settings and the native workspace quota. Confirm peak RAM and runtime as well
   as storage, including aggregation and large download preparation.
2. Measure simultaneous configurations for the four-user planning target, with
   retained outputs and normal service overhead present. Confirm practical
   concurrency, disk layout, retention and resource recovery ceilings.
3. Register an approved immutable release/resource policy and verified inputs
   for the supported workload only after those measurements. Existing prepared
   datasets and queued jobs keep their recorded policies.
