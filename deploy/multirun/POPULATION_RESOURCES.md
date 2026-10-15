<!-- (C) Copyright 2026, by Ross Richardson
Population/year memory profiling method, unattended commands and evidence limits.
@author ross richardson
-->

# Population and simulation-year resource profiles

## Purpose and status

Prepared on 15 October 2026. Native scientific measurements are pending. This is
a separate test campaign from the SingleRun memory and Visualiser capacity tests;
run them sequentially on an otherwise idle laptop.

The first campaign measures **20,000, 50,000 and 100,000 initial people**, each
through **2070**, using the same model, source inputs, seed and resource limits.
It records resources throughout each run and summarises **2030, 2040, 2050, 2060
and 2070**, together with every intervening annual boundary. This will help choose
which smaller memory allocations to test next. Production defaults are unchanged.

| Control | Default |
| --- | --- |
| Country and first year | UK, 2019 |
| Starting populations, in order | 20,000; 50,000; 100,000 |
| Final year | 2070, all 52 annual years |
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

## Inputs, disk space and cleanup

All populations use the **current frozen `multirun.jar`**. Fresh public training
inputs are prepared and checked in an independent JVM for each population.
`--runtime-from` reads only the image ID from an older private calibration
receipt. It does not reuse that receipt's model JAR or prepared data. Consequently,
these new results must not be treated as direct timing repetitions of older A–E
trials that used a different JAR.

For each population the driver performs:

1. Freeze/verify the selected code, model and public source-input fingerprints.
2. Prepare and independently validate fresh inputs in a new private directory.
3. Wait 60 seconds for preparation activity to settle.
4. Run one fixed-allocation configuration through the existing disposable
   PostgreSQL/worker/Docker/browser proof. No real emails are sent.
5. Verify completion, annual files, seeds, settings, hashes and released capacity.
6. Stream an independent annual file audit and create compact evidence archives.
7. Verify the archives, dispose of this case's manifested bulk outputs, and
   retire this campaign's generated prepared-input files before the next case.

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
  --output "/tmp-codex/population-year-plan-$(date +%Y%m%d-%H%M%S)" \
  --dry-run
```

Before the long campaign, run this short native smoke check in your terminal.
It prepares a small population and simulates 2019–2021, exercising the genuine
model/year observer and the complete archive/cleanup path:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/research_population_profile.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --populations 1000 --end-year 2021 --pause-seconds 0 \
  --output "/tmp-codex/population-year-smoke-$(date +%Y%m%d-%H%M%S)"
```

After a successful smoke check, start the overnight campaign when the other
tests have finished. This runs all three populations automatically:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/research_population_profile.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --populations 20000 50000 100000 \
  --output "/tmp-codex/population-year-resources-$(date +%Y%m%d-%H%M%S)"
```

The campaign can take several hours, including fresh preparation; no overnight
completion time is guaranteed. Leave the terminal and laptop running. Each
population's run output is also saved under `logs/`.

## Reading the results and follow-up work

- `report.json`: overall outcome and completed stages; on failure, the stage and
  error are retained without attempting another population.
- `comparison.md` and `population-resources.csv`: decade comparisons across
  populations, with interval/cumulative memory peaks, population counts and times.
- `archives/population-N/population-profile.json`: complete annual and decade
  numeric summaries, including CPU, throttling and GC counters.
- `archives/population-N/year-samples.jsonl`: the original bounded numeric stream.
- `archives/population-N/archive.json`: original completion reports, manifest
  hashes, per-file sizes and annual rows/bytes without the raw scientific rows.

**Still to do after measurement:**

- Test promising smaller heaps/containers explicitly, including possible
  20,000-person settings, before adopting population-specific production limits.
  A run constrained by a 4 GiB heap does not reveal the minimum safe heap.
- Validate shorter stopping horizons independently. Termination-only cleanup or
  persistence can differ from a long simulation passing through the same year.
- Check additional seeds, ten repetitions, demanding permitted policy settings
  and population-growth patterns. Memory need not increase monotonically with
  year, and initial population does not fully predict later population demand.
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
  deploy.multirun.test_research_population_profile -v
```
