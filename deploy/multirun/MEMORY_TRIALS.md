<!-- (C) Copyright 2026, by Ross Richardson
Smaller heap/container qualification with sequential repetitions in one JVM.
@author ross richardson
-->

# Smaller-memory and repeated-JVM trials

Prepared on 19 October 2026 after the six population/horizon measurements in
[POPULATION_RESOURCES.md](POPULATION_RESOURCES.md). This campaign qualifies
candidate limits; it does not change production allocations or start simulations
until the operator runs the commands below.

Validation on 19 October: **154 focused checks passed**, including real Java
fixture lifecycle checks, unchanged production policy minima, smaller-container
staging, same-JVM reporting and guarded failure/archive/input retirement. The
six-case plan passes the current disk/RAM preflight and selects the same model JAR
and runtime image ID as the preceding population campaign. The new scientific
smoke and overnight cases still need the operator's terminal; Docker is not
accessible from the coding sandbox. Local check evidence is in
`/tmp-codex/population-memory-harness-20261019/report.json`.

## Tonight's cases

Each case runs **two repetitions, seeds 606 and 607, sequentially in the same
JVM**. Separate cases have separate JVMs. A fixed **2-vCPU Docker quota** and
Java processor count of two keep CPU settings constant while memory is reduced.
Each case has one attempt, with memory/storage expansion and retries disabled.
At least 1 GiB remains between maximum heap and the container memory limit.

| Requested initial population | Actual stopping year | Maximum heap | Container RAM | Repetitions in one JVM |
| ---: | ---: | ---: | ---: | ---: |
| 20,000 | 2035 | 2.5 GiB | 3.5 GiB | 2 |
| 20,000 | 2070 | 2.5 GiB | 3.5 GiB | 2 |
| 50,000 | 2035 | 3 GiB | 4 GiB | 2 |
| 50,000 | 2070 | 3 GiB | 4 GiB | 2 |
| 100,000 | 2035 | 3 GiB | 4 GiB | 2 |
| 100,000 | 2070 | 3.5 GiB | 4.5 GiB | 2 |

The final case is exploratory; the 4 GiB heap / 5 GiB container remains the
tested reference until smaller profiles are qualified. A successful case covers
these two seeds and scientific settings, not all population paths or ten
repetitions. Earlier observed peaks under larger limits do not establish smaller
safe limits, because GC and memory use can change with the available heap.

`--memory-trials` selects the table's sizes for the requested population/horizon
subset and requires `--repetitions 2`. Do not combine it with custom heap/container
arguments. Without the flag, the existing profiling driver still accepts explicit
heap/container settings and retains its original defaults.

## What the repeated run measures

The native proof verifies a single observed process identity, an unbroken passive
sample stream, the expected seed sequence, all requested annual files, frozen
settings, hashes and released capacity. The runner uses `exec` to launch Java;
the container's observed init PID therefore identifies that Java process.

The additional report derives these values from existing two-second samples and
annual/process markers; it adds no forced GC and no work to the scientific model:

- Separate first/second setup and simulation/output timings, memory peaks, GC
  collection counters, and mean CPU usage.
- First-year memory after population build, and memory at the run-finish marker
  **before** disposal. The second setup interval includes disposal of the first
  run and the next population build.
- Lowest sampled heap and working RAM during unassigned between-run timer
  observations, with committed heap shown separately. If no such timer sample was
  observed, the value is unavailable, not zero.
- Whole-JVM peaks, including its finalisation/shutdown sample, plus the existing
  annual/decade, actual population and workspace observations.

Between-run observations are not a guaranteed post-GC idle live-set measurement:
the next build can already be under way. Committed heap need not return to the OS
for objects to have been reclaimed and memory to be reusable inside the JVM.
Different seeds can also have different demands. Two repetitions can reveal a
failure or accumulating demand, but cannot prove the absence of a slower leak.

Keep 2 vCPUs for tonight's memory comparison. Before adopting a successful profile
at **1.5 or 1 vCPU**, qualify the chosen profile with two sequential repetitions
at that CPU quota. Repeating the entire memory matrix is unnecessary. Reduced CPU
can slow GC and change memory peaks. Java's processor-count setting remains two;
the separate future test of JVM automatic CPU detection remains outstanding.

## Inputs, evidence and unattended failure handling

Each population gets freshly prepared public training inputs using the current
frozen `multirun.jar`; the same verified package is reused for its two horizons.
`--runtime-from` supplies only the old package's immutable runtime image ID.
It does not reuse that package's older model or prepared inputs. The preparation
process retains its existing memory allocation; smaller limits apply to the
simulation JVM, not to input preparation.

The earlier reference is
`/tmp-codex/population-year-resources-20261018-235354/comparison.html`.
That campaign used one repetition, seed 606. Compare like-for-like first-repetition
timings with due allowance for independently generated inputs and laptop/runtime
variation; do not compare its single-run time directly with tonight's two-run
total. Every campaign records model/input/runtime/source identities for review.

Successful output is independently audited, compact numeric/hash evidence is
archived and reopened, and only then are this campaign's bulky outputs disposed.
Generated input bulk is retired after that population's last case. Model JARs,
receipts, source-input hashes, settings, telemetry, annual counts and reports remain.
Existing source inputs, older evidence and the original runtime are preserved.

A candidate may legitimately fail with the selected smaller allowance. Only a
native classified `heap_limit` or `memory_limit` permits the next independent case
to start, and only after evidence confirms one finished attempt, unchanged inputs,
no published partial results, released reservations, removed scratch and successful
disposable-service cleanup. Partial samples and private diagnostics are archived;
they are never presented as completed-run peaks. No candidate is retried.

All other errors, missing evidence, changed sources or uncertain cleanup stop the
campaign and preserve evidence/inputs for investigation. A fully traversed campaign
sets `campaign_completed=true`; if any candidate was rejected, `passed=false` and
the final exit code is nonzero. That is not a claim that the rejected candidate
passed simply because later cases completed.

## Space and expected time

Before each fresh preparation, the conservative preflight requires **19 GiB free
disk** and at least **7 GiB available RAM**, including the preparation envelope.
The trial's storage allowance is 12 GiB: 4 GiB setup plus 4 GiB per repetition.
After preparation, the admission check requires 15 GiB still free. Input reuse and
verified disposal keep the six cases sequential rather than accumulating bulk.

On 19 October, the previously retained two-repetition alternative raw output was
losslessly compressed and every original file hash verified before reclamation.
This freed approximately 4.76 GiB, leaving about 19.96 GiB free. Its recovery copy
and restoration instructions are in `/tmp-codex/storage-cleanup-20261019/README.md`;
the baseline recovery archive remains under `storage-cleanup-20261018`. Keep both
archives. Future raw-output tests using those paths require restoration and space.

The earlier six single-repetition Java lifetimes totalled about 3 hours 34 minutes.
Doubling that gives about 7 hours 8 minutes of model/JVM time before preparation,
staging, dependencies, verification and archival. Budget **8–10 hours** for the
main campaign; smaller heaps, GC behaviour or laptop conditions may extend this.
The short two-repetition smoke below is additional. These are estimates, not
guaranteed completion times.

## Commands

Keep networking enabled for isolated test dependencies, use mains power, prevent
sleep and stop other heavy work. No new runtime image build is needed if the
referenced image remains installed. Do not edit the frozen source/model/input
files while a campaign is running.

Optional plan inspection, with no preparation, Docker access or simulation:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/research_population_profile.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --memory-trials --repetitions 2 \
  --populations 20000 50000 100000 --end-years 2035 2070 \
  --output "/tmp-codex/population-memory-plan-$(date +%Y%m%d-%H%M%S)" \
  --dry-run
```

Run a short genuine two-repetition smoke first, then automatically start the
overnight matrix only if it passes:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/research_population_profile.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --populations 20000 --end-year 2021 --repetitions 2 \
  --heap-mib 2560 --memory-mib 3584 --cpu-millis 2000 --pause-seconds 0 \
  --output "/tmp-codex/population-memory-smoke-$(date +%Y%m%d-%H%M%S)" &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/research_population_profile.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --memory-trials --repetitions 2 --cpu-millis 2000 \
  --populations 20000 50000 100000 --end-years 2035 2070 \
  --output "/tmp-codex/population-memory-$(date +%Y%m%d-%H%M%S)"
```

The smoke uses a real 20,000-person model; do not substitute the failed
1,000-person empty-population case. Its short horizon exercises sequential
repetitions, smaller-container staging and archive/cleanup; it does not certify
the 2035 or 2070 workloads.

Read `memory-comparison.html` and `memory-repetitions.csv` in the matrix output
directory. `comparison.html` retains detailed annual/phase/storage tables and plots.
Each case also has native, audit and archive directories plus a private run log.
Partial failed-candidate evidence is under its archive's `failed-memory-case.json`.
