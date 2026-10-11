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

## Remaining host checks

1. On the selected host, measure ten repetitions, different permitted policy
   settings and the native workspace quota. Confirm peak RAM and runtime as well
   as storage, including aggregation and large download preparation.
2. Measure simultaneous configurations for the four-user planning target, with
   retained outputs and normal service overhead present. Confirm practical
   concurrency, disk layout, retention and resource recovery ceilings.
3. Register an approved immutable release/resource policy and verified inputs
   for the supported workload only after those measurements. Existing prepared
   datasets and queued jobs keep their recorded policies.
