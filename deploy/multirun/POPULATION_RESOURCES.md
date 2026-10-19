<!-- (C) Copyright 2026, by Ross Richardson
Population/year memory profiling method, unattended commands and evidence limits.
@author ross richardson
-->

# Population and simulation-year resource profiles

## Purpose and status

Prepared on 15 October 2026; measurement/reporting and paired horizons updated on
18 October 2026. All six native scientific cases passed on 19 October 2026. This is
a separate test campaign from the SingleRun memory and Visualiser capacity tests;
run them sequentially on an otherwise idle laptop.

The agreed overnight campaign measures **20,000, 50,000 and 100,000 initial people**,
each **ending in 2035 and ending in 2070**, using the same model, source inputs,
seed and resource limits. These are six separate simulations, one repetition each.
Each population is prepared once and its exact verified input package is reused
for both horizons. It records resources throughout each run and summarises
**2030, 2035, 2040, 2050, 2060 and 2070** when within the selected horizon,
together with every intervening annual boundary. This will help choose
which smaller memory allocations to test next. Production defaults are unchanged.

Validation on 18 October: 94 focused regression checks pass, covering compilation
against the current model API, real Java shutdown-hook sampling with fictional
models, paired-input reuse, labelled reports, and archive/cleanup failure guards.
These include a real Java child-thread failure with a surviving non-daemon waiter:
both test wrappers now exit nonzero promptly instead of hanging.
The six-case read-only plan passes the disk/RAM preflight before model work starts.
The corrected smoke passed and the six-case native campaign completed. See the
measured results below and the preserved first smoke failure further down.

| Control | Default |
| --- | --- |
| Country and first year | UK, 2019 |
| Starting populations, in order | 20,000; 50,000; 100,000 |
| Final year | CLI default: 2070, all 52 annual years; tonight: 2035 and 2070 in separate runs |
| Repetitions per population | One, seed 606 |
| Maximum Java heap | 4 GiB |
| Fixed container RAM | 5 GiB |
| Docker CPU allowance | 2 vCPUs; Java processor count remains two |
| Working-storage allowance | 4 GiB fixed + 4 GiB per repetition |
| Resource growth and retries | Disabled; one attempt |
| Runtime budget | 15 minutes setup + four hours per repetition |

One repetition keeps the initial campaign manageable. `--repetitions 2` runs
seeds 606 and 607 sequentially in the same JVM for each population, increasing
both runtime and the storage preflight requirement. It can expose additional
retained-memory demand between repetitions, but is not a ten-repetition proof.

## Measured results — 19 October 2026

Evidence: `/tmp-codex/population-year-resources-20261018-235354/report.json`, with
`comparison.html`, original numeric archives and per-case native/audit reports
alongside it. All six cases passed completion, source/seed/settings, annual output,
hash, archive, input retirement and released-capacity checks. Each used seed 606,
two vCPUs, a 4 GiB maximum heap and a fixed 5 GiB container. Each had one attempt;
none increased its allocation or retried. Total campaign wall time, including
preparation, verification and disposal, was **4 hours 34 minutes**.

Java time below is from the passive Java process-start observation to its shutdown
sample. It includes model initialisation, simulation, annual output work and
observed finalisation, but excludes external input preparation, container staging
and post-run verification. Working memory subtracts inactive file cache. Peaks
are sampled observations under the tested limits, not minimum safe allocations.

| Requested initial people | Actual ending year | Java time (min) | Peak heap (GiB) | Peak working RAM (GiB) | Retained output (GiB) | Sampled workspace peak (GiB) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20,000 | 2035 | 7.4 | 2.14 | 2.81 | 0.194 | 4.64 |
| 20,000 | 2070 | 18.6 | 2.33 | 3.00 | 0.653 | 4.93 |
| 50,000 | 2035 | 16.4 | 2.72 | 3.26 | 0.503 | 4.80 |
| 50,000 | 2070 | 42.9 | 2.69 | 3.21 | 1.706 | 6.01 |
| 100,000 | 2035 | 37.4 | 2.82 | 3.36 | 1.019 | 5.30 |
| 100,000 | 2070 | 91.1 | 3.23 | 3.79 | 3.465 | 7.75 |

Actual prepared starting counts were **18,883; 48,917; 99,009**. Largest observed
annual-boundary model collection sizes through 2070 were **22,825; 59,249;
119,813** respectively. Requested size must not be presented as the exact realised
population. Within each population pair, all annual exported row counts match
through 2035. Every annual CSV differs in the audit's through-2035 row-byte total
by one byte at 2035 only, consistent with `ExportCSV` placing a newline before
the following row in the continuing run. Earlier annual row-byte totals match.
This is not a separate proof of full scientific value equality across horizons.

Findings and limits:

- The 4/5 GiB heap/container profile completed the tested 100,000-person workload
  even as the observed model population approached 120,000. It remains evidence
  for this seed/settings/repetition count, not for all permitted population paths.
- Memory did not simply increase with the stopping year. For example, the
  50,000-person 2035 case had a slightly higher sampled peak than its 2070 pair.
  Java allocation/GC and cache behaviour vary. Smaller heaps/containers must be
  tested explicitly; a 20,000-person peak of 2.33 GiB under a 4 GiB heap does not
  establish either failure or safety with a 2 GiB heap.
- No substantial new finalisation-only working-memory spike was observed in the
  2035 cases. Their finalisation intervals were approximately 1.0–1.7 seconds;
  the 2070 intervals were approximately 1.2–1.9 seconds. Other shutdown hooks
  and sample intervals remain limitations.
- Mean whole-process container CPU use was **1.59–1.75 vCPUs**, including Java
  helper/GC work. These measurements should not be interpreted as evidence that
  a 1-vCPU quota would leave completion time unchanged. GC collection-counter /
  wall-time ratios were approximately **2.1–2.9%**, not application pause fractions.
- The 100,000/2070 workspace reached **7.75 GiB**, close to its configured 8 GiB
  allowance. One running storage-pressure event recorded `action=waiting` at the
  fixed ceiling; the allocation stayed at 8 GiB and the job continued successfully.
  Do not describe this as an allocation increase, failed attempt or job queued
  for storage. The rehearsal uses application monitoring, not an enforced kernel
  workspace quota. Future sizing should allow more headroom and/or retain the
  production bounded-growth safeguard.
- The largest observed disk growth, approximately **2.76–2.77 GiB**, occurred in
  setup while staging inputs. Restricting adjacent samples to assigned simulation
  years, the largest positive net growths through 2070 were approximately **14.09,
  37.30 and 76.07 MiB** over roughly 10.3–10.5 seconds for the three sizes. Temporary
  bursts can be missed; this alone does not approve a smaller storage-growth step.
- Ten repetitions at 100,000/2070 would retain roughly **34.65 GiB** if outputs
  scale similarly, supporting the earlier approximate 35 GiB planning figure.
  This is an extrapolation, not a measured ten-repetition result; shared setup,
  temporary workspace and Visualiser data need separate allowances.

The next campaign now combines explicit smaller-heap/container trials with two
sequential repetitions in one JVM for each population/horizon. See
[MEMORY_TRIALS.md](MEMORY_TRIALS.md) for the six candidate profiles, same-JVM
diagnostics, guarded failure handling and commands. Keep 2 vCPUs to isolate memory
changes; later qualify only the selected profiles at lower CPU quotas with two
repetitions, rather than repeating the entire matrix. Hosted mixed-load acceptance
is still needed before approving concurrency. Production defaults were not changed.

## Measurements

The test-only Java wrapper delegates to the unchanged native MultiRun entry
point. It forwards the model's existing year messages and observes collection
sizes on the model thread. It does not alter scientific settings, random draws,
collectors, event schedules or garbage-collection policy. The periodic sampler
uses cached population counts; it never walks mutable population collections.

Every two seconds, plus annual boundaries, the numeric stream records:

- Heap used, committed and maximum; non-heap memory; container usage, inactive
  file cache and working memory; the kernel's cumulative container-memory peak
  where available.
- GC collection-time counters, process CPU time and cgroup CPU/throttling
  counters. Unsupported counters remain unavailable rather than being set to zero.
- Repetition, model year and last observed person/benefit-unit/household counts.

The next year's start closes the previous year's interval, including scheduled
annual output work after the model's own finish message. An engine End listener
closes the final year before MultiRun disposes of the model. Setup observations
are kept separately; cumulative peaks include initialisation. The ordinary
calibration telemetry and whole-attempt measurements remain available too.

The independent output audit verifies hashes and counts the exported Person rows
for every year. Reports distinguish these exported counts from model collection
sizes, which can include exited objects awaiting cleanup. Neither is an exact
maximum of the live population at every instant within a year.

Decade reports include both the interval peak and the cumulative peak through
that year. An early memory spike remains visible even if usage falls later.
Heap and working-memory peaks are sampled; short spikes may be missed. Kernel
memory.peak covers the entire process/container lifetime and includes file cache.
GC time is a JVM counter, not a promise of recoverable wall-clock time.

### Additional lightweight measurements and reporting

- The existing workspace metadata walk (normally every ten seconds) now retains
  its size/allocation snapshots, including input, CSV, copied-input, other and
  request bytes. It reads no CSV contents. Reports identify the largest positive
  net growth between adjacent observations and the actual sampling gap. This is
  not a bound on short-lived temporary files, instantaneous write bursts or
  kernel I/O bandwidth. Disk observations refer to the latest received Java year
  sample; they are not atomic observations of disk usage and model year.
- Annual and milestone reports calculate mean container vCPUs used, percentage
  of the selected CPU allowance used, GC collection-counter/wall-time ratios and
  percentage of quota periods throttled. Unavailable counters remain unavailable.
  GC ratios are not application pause percentages; cgroup quota throttling does
  not measure contention with other VM customers.
- Phase tables separate first initialisation, work between year-start and
  year-finish messages, work after year-finish, between-repetition work when
  present, and finalisation. After-year work can include scheduled CSV output;
  it is not exclusively disk I/O. Phase endpoints share samples and short gaps
  between markers are possible.
- A passive Java shutdown hook records the final process sample. MultiRun starts
  a separate thread and eventually calls `System.exit`, so returning from its
  `main` method cannot serve as the completion marker. The hook requests no GC,
  touches no population collections and changes no model controls. Other shutdown
  hooks may run concurrently; this is not a proof of a post-exit memory peak.
  Native v2 year evidence must include this marker before it can pass or be
  archived. Historical A–E results are not rewritten.
- The test wrapper now installs an uncaught-thread failure handler that prints
  the exception to its private stderr and exits nonzero. A dead engine cannot
  leave a surviving MultiRun/telemetry JVM appearing active for hours. This is a
  test-launcher safeguard; scientific model/collector rules are unchanged.
  Progress messages and `progress.json` also identify the latest observed year,
  population and seconds since the last year/process boundary.
- Labelled HTML tables and offline SVG plots show annual memory/runtime against
  the observed population, and compare a complete run ending in 2035 with a run
  passing through 2035 on the way to 2070. They compare observed demand under the
  tested allowances, not minimum required memory. Annual CSV storage increments
  come from the existing post-run verified audit; the first year includes headers.

## Inputs, disk space and cleanup

All populations use the **current frozen `multirun.jar`**. Fresh public training
inputs are prepared and checked in an independent JVM for each population.
`--runtime-from` reads only the image ID from an older private calibration
receipt. It does not reuse that receipt's model JAR or prepared data. Consequently,
these new results must not be treated as direct timing repetitions of older A–E
trials that used a different JAR.

For each population the driver performs:

1. Freeze/verify the selected code, model and public source-input fingerprints.
2. Prepare and independently validate fresh inputs in a new private directory
   for this population, or reverify its existing package for the second horizon.
3. After fresh preparation, wait 60 seconds for activity to settle.
4. Run one fixed-allocation configuration through the existing disposable
   PostgreSQL/worker/Docker/browser proof. No real emails are sent.
5. Verify completion, annual files, seeds, settings, hashes and released capacity.
6. Stream an independent annual file audit and create compact evidence archives.
7. Verify the archives and dispose of this case's manifested bulk outputs.
   Retain prepared inputs until both horizons finish, then retire only this
   campaign's generated input files before preparing the next population.

Reports, receipts, numeric observations, hashes, file sizes, annual row counts
and the frozen model are retained. Existing benchmark inputs and source `input/`
files are not deleted or rewritten. A retired generated package cannot be reused
without fresh preparation. Failed or uncertain cases stop the campaign, preserve
their diagnostic evidence, and do not launch the next population.

The default campaign requires **15 GiB free disk and 7 GiB available RAM** before
each preparation. Two repetitions require 19 GiB free disk. Calibration rechecks
space after preparation. These are test admission margins, not measurements of
the eventual production requirements. A campaign lock excludes other research
sequences using the same output parent; it cannot exclude unrelated heavy work.

Keep networking enabled: the disposable proof installs isolated dependencies.
Do not run this alongside the SingleRun or aggregation tests, and avoid changing
the frozen application/model/input files while the campaign is running. No new
Docker image build is required if the referenced runtime image remains installed.

## Commands

First check the plan; this does not prepare data, access Docker or run a model:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/research_population_profile.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --end-years 2035 2070 \
  --output "/tmp-codex/population-year-plan-$(date +%Y%m%d-%H%M%S)" \
  --dry-run
```

Before the long campaign, run this short native smoke check in your terminal.
It prepares 20,000 requested people and simulates 2019–2021, exercising the genuine
model/year observer and the complete archive/cleanup path:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/research_population_profile.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --populations 20000 --end-year 2021 --pause-seconds 0 \
  --output "/tmp-codex/population-year-smoke-$(date +%Y%m%d-%H%M%S)"
```

After a successful smoke check, start the overnight campaign when the other
tests have finished. This runs all three populations at both ending horizons:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/research_population_profile.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --populations 20000 50000 100000 \
  --end-years 2035 2070 \
  --output "/tmp-codex/population-year-resources-$(date +%Y%m%d-%H%M%S)"
```

The campaign can take several hours, including fresh preparation; no overnight
completion time is guaranteed. Leave the terminal and laptop running. Each
case's run output is also saved under `logs/`.

Order: 20,000 through 2035, 20,000 through 2070, then the corresponding pair for
50,000 and 100,000. Only one model runs at a time. Each run has its own JVM; paired
horizons are not two repetitions of one simulation. The smoke check above is
still recommended before this campaign. `--end-year` and `--end-years` are mutually
exclusive; the latter accepts up to three distinct ending years.

The earlier quiet 100,000-person logs measured approximately 33 minutes of model
annual work through 2035, plus initialisation and verification. On that evidence,
the three additional shorter cases are estimated to add about 1.5–2.5 hours to
the 4–5 hour initial estimate: roughly 6–7.5 hours, with eight hours allowed.
This is an estimate from an earlier build, not a measurement of the current
profiler. Network/dependency setup, preparation, auditing and laptop conditions
can extend it. Paired input reuse adds no concurrent disk requirement: the
one-repetition campaign still needs 15 GiB free before fresh preparation.

## Reading the results and follow-up work

### First native smoke failure: 18 October

`/tmp-codex/population-year-smoke-20261018-230421` requested 1,000 people, but its
verified prepared population contained only 44 after the model's population
targeting. By the end of 2020 it had no households. The native collector threw
`IllegalStateException: Median could not be calculated — totalWeight=0.0,
households considered=0`. Its engine thread died, while other JVM threads remained
alive. The 2019 and 2020 annual model timers were 0.025 and 0.019 seconds: the
apparent twenty-minute run was not evidence of slow model throughput. Preparation
itself took approximately eight minutes including copy/verification overhead.

Read-only Docker logs/process diagnostics were saved as
`/tmp-codex/population-smoke-container-20261018.log` and
`/tmp-codex/population-smoke-processes-20261018.txt`. The user interrupted the failed
smoke; the guarded six-case scientific campaign did not start. Do not relax the
collector's empty-population error or count this run as a resource benchmark.
The replacement smoke uses the main campaign's smallest requested population,
20,000. Its actual prepared population is recorded rather than assumed to equal
the requested number. The broader scientific empty-population behaviour remains
a model-maintenance question, separate from this profiler's failure handling.

After confirmed native cleanup, the failed smoke's 62 generated public input
files were checksum-verified and retired to restore disk headroom (2.728 GiB
reclaimed). Its model JARs, profiles, input manifest/receipt and logs were retained;
`prepared/population-1000/package/failed-input-cleanup.json` records the operation.
That retired input package must not be reused without fresh preparation.

### Report files

- `report.json`: overall outcome and completed stages; on failure, the stage and
  error are retained without attempting another population.
- `comparison.md` and `population-resources.csv`: decade comparisons across
  populations and ending horizons, with interval/cumulative memory peaks,
  population counts and times. 2035 is included as an additional milestone.
- `comparison.html`: labelled annual/phase/storage tables, observed-population
  scatter plots and the short-run versus through-year comparison. Works offline.
- `population-annual.csv`: every year, population, actual ending horizon and seed,
  with memory peaks, CPU/GC/throttling ratios and audited annual/cumulative CSV sizes.
- `archives/population-N/population-profile.json`: complete annual and decade
  numeric summaries, including CPU, throttling and GC counters.
- `archives/population-N/year-samples.jsonl`: the original bounded numeric stream.
- `archives/population-N/workspace-samples.jsonl`: original bounded stat-only
  workspace history. With several horizons, archive names include `-through-YEAR`.
- `archives/population-N/archive.json`: original completion reports, manifest
  hashes, per-file sizes and annual rows/bytes without the raw scientific rows.

**Still to do after measurement:**

- Test promising smaller heaps/containers explicitly, including possible
  20,000-person settings, before adopting population-specific production limits.
  A run constrained by a 4 GiB heap does not reveal the minimum safe heap.
- Review the independent 2035 finishing cases before assuming that passing
  through a year predicts the resources needed to finish there. Test further
  stopping horizons if needed.
- Check additional seeds, ten repetitions, demanding permitted policy settings
  and population-growth patterns. Memory need not increase monotonically with
  year, and initial population does not fully predict later population demand.
- A small two-repetition run can check cleanup and the repetition lifecycle, but
  does not expose all retained-memory or GC demands of a large/long simulation.
  Validate smaller allowances using representative repetition workloads before
  adopting them for production. These six one-repetition cases alone do not
  establish the safety of ten sequential repetitions in the same JVM.
- Repeat on the chosen VM and measure concurrent workload throughput and
  responsiveness. Laptop measurements do not establish hosted concurrency.
- Use those results to approve versioned resource profiles; retain live growth
  and bounded resource-specific recovery as production safeguards.

Focused regression checks use fictional outputs and a tiny Java lifecycle
fixture. They require Java 17+ tools but do not launch scientific simulations:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python -m unittest \
  deploy.multirun.test_research_year_profile \
  deploy.multirun.test_research_population_profile \
  deploy.multirun.test_research_profile_reporting -v
```
