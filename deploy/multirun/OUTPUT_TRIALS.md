<!-- (C) Copyright 2026, by Ross Richardson
Matched scientific screening of optional Gini calculation costs.
@author ross richardson
-->

# Optional output calculation trials

Prepared on 20 October 2026. The completed smaller-memory campaign enabled all
standard annual CSV exports, including Household and the regular statistics
files. It disabled `calculateGiniCoefficients` and `exportToDatabase`. This small
follow-up changes **only the Gini flag** in paired comparisons, with a fresh
reference in every pair. It also supports one selected mode and two sequential
repetitions; it does not enable database output or change production profiles.

Decision on 20 October: database export is excluded from the online MultiRun
service and is fixed off in its configuration validator and browser form, including
imports of native/web YAML. This leaves Gini as the optional output calculation
to screen here. It does not change the desktop SimPaths database-export option.
The service continues to produce private raw CSVs for aggregation; existing
authorisation and provider-data download restrictions are still required to
prevent microdata access. Disabling database export alone is not that boundary.

## Workload and comparison

The default is 20,000 requested people, 2019–2021 inclusive, seed 606, one
repetition per fresh JVM, fixed 2 vCPUs, a 2.5 GiB maximum heap and a 3.5 GiB
container. It uses the same installed runtime as the earlier research tests,
the current frozen `multirun.jar`, and freshly prepared public training inputs
shared by every condition. It inherits only the runtime from `--runtime-from`;
it does not change that reference package or reuse its original model JAR.

Three paired rounds run off/on, on/off, off/on. Results show individual values,
per-condition medians and ranges, and each paired difference. With an odd number
of pairs the order is not perfectly balanced. These are screening observations,
not a statistical significance test or a proof about every seed/settings choice.
`--modes on` selects Gini-on alone; `--modes on off` specifies the order rather
than alternating it. `--repetitions 2` runs seeds 606 and 607 in the same normal
Java MultiRun process. Rounds are separate JVM launches; repetitions are not.

**Why start at 20,000?** The current source's
`SimPathsCollector.GiniPersonalGrossEarnings.update()` and
`GiniEquivalisedHouseholdDisposableIncome.update()` contain nested pairwise
loops over eligible people and benefit units. The calculation work can grow
approximately with the square of the eligible population: a fivefold increase
in eligible people can require roughly 25 times as many person comparisons.
This does not mean the complete simulation takes 25 times longer, and actual
cost must be measured. A short 100,000-person follow-up remains necessary before
claiming that Gini costs are negligible at the research workload boundary.

The test records:

- Whole-JVM elapsed time, plus simulated-year/output time separately from setup.
- Sampled heap, working-memory and total charged-memory peaks, GC collection
  counters, mean CPU use, workspace peak and retained output size.
- Actual finite national Gini values from each year's small
  `WealthIncomeStatistics.csv`. Off must remain zero; on must produce nonzero
  values. A changed flag without actual calculation does not pass.
- Immutable image, model, input, scientific settings and observer identities.
  Paired configurations must differ only in `calculateGiniCoefficients`.
- Verified annual outputs, hashes, original seed, fixed limits, one attempt,
  container removal, capacity release and cleanup, using the existing proof.

No forced GC, heap dumps or raw-file parsing are added inside the timed model.
The same passive observer runs in both conditions. Working RAM subtracts inactive
file cache; it is not the entire charged container memory or a minimum safe limit.
GC counter time is not application pause time. CSV checksums are not required
to match between conditions: Gini values change deliberately, and the known
parallel benefit-receipt random-draw issue can affect other scientific values.

## Run locally

Use the terminal: Docker, native sockets and Chromium are unavailable in the
coding sandbox. Leave networking enabled for the disposable dependency setup;
no images need rebuilding. Keep other heavy work stopped for the comparison.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/research_output_trials.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --population 20000 --end-year 2021 --rounds 3 \
  --output "/tmp-codex/optional-gini-$(date +%Y%m%d-%H%M%S)"
```

For a first functional pair, use `--rounds 1`; this still runs both conditions.
For a plan without preparation, model execution or Docker access, add `--dry-run`.
For the later larger screening, change only `--population 100000` and choose a
new output directory. This selects the previously tested 3 GiB heap / 4 GiB
container for the short horizon. A longer horizon after 2035 selects the measured
3.5 GiB heap / 4.5 GiB container for that population.

Preparation requires 15 GiB free disk and 7 GiB available RAM for the default
case; preflight checks these again as appropriate. The model has 15 minutes for
setup plus 30 minutes for its one repetition. A larger Gini workload may exceed
that deliberate screening limit; its failure remains a result, and the tool
stops rather than quietly extending limits or retrying. Allowance plus bounded
driver/cleanup grace is not an estimate of typical completion time. The first
20,000-person pair completed in 21.5 minutes including preparation and verification;
the Gini-on JVM took 4 minutes 25 seconds. The completed 50,000-person short
follow-up is recorded below. Review the screening deadline before any longer
follow-up: pairwise scaling can exceed it without indicating a memory failure.

Each condition's bulk output is independently audited and hash-checked, compact
evidence is re-opened, and only then are its bulk files removed. The shared new
prepared inputs are retired only after every condition succeeds. A failed
condition, mismatched pair, unexpected Gini output or uncertain cleanup stops
the campaign and preserves the relevant evidence and remaining inputs.

Open `comparison.html` in the output directory for labelled tables;
`comparison.csv` and `report.json` retain the numeric observations, paired
differences and Gini validation. `repetitions.csv` and the HTML's repetition and
transition tables distinguish build/run times, peaks and GC counters within the
same JVM. They are calculated after execution from the existing passive stream.
Original completion evidence and passive
year/workspace samples remain under `archives/`. Interpret only completed native
conditions; interrupted or failed conditions retain their own diagnostics.
**Do not rely on Ctrl+C to preserve completed repetitions within an unfinished
condition.** Native cancellation cleanup can remove that workspace. A completed
earlier condition already verified and archived remains preserved, but the two
repetitions within one condition must both finish to obtain its full archive.

## Two-repetition overnight laptop test

The agreed next test is **50,000 people, 2019–2070, Gini on, two sequential
repetitions in the same JVM**, fixed 2 vCPUs, 3 GiB heap and 4 GiB container.
It uses the normal model Gini flag and MultiRun seed plan, with the existing
passive year/resource probe. No output hashing, CSV scanning, file copying or
forced GC is inserted between repetitions. The full output audits and Gini
checks for **both** seeds run only after the entire model condition finishes.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python deploy/multirun/research_output_trials.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --runtime-from /tmp-codex/research-prepared-100000-20261009-232734/package \
  --population 50000 --end-year 2070 \
  --modes on --rounds 1 --repetitions 2 \
  --repetition-seconds 28800 \
  --output "/tmp-codex/optional-gini-50k-2070-two-reps-$(date +%Y%m%d-%H%M%S)"
```

Budget roughly **10–12 hours**, potentially longer: extrapolating three measured
Gini-on years gives about 5.2 hours per repetition, but changing population/yearly
work and laptop conditions make that provisional. The explicit deadline is
**15 setup minutes + 2 × 8 hours = 16 hours 15 minutes** for the model condition,
plus 30 minutes of proof/cleanup allowance. This is a cumulative condition budget,
not a separate per-repetition kill timer. The overall campaign also includes
preparation, dependency/database/browser setup and audits outside the Java timer.

The two-repetition storage allowance is 12 GiB. Initial preflight requires
**19 GiB free disk and 7 GiB available RAM**, including preparation and test-service
headroom, and fails before starting if unavailable. Networking must be available
for dependency/credential setup. Use mains power, disable sleep, and avoid other
substantial work or edits to the frozen sources/model/input files during the run.
No image or scientific JAR rebuild is required. To inspect the plan only, append
`--dry-run` and use a new output path.

The existing two-repetition Java/memory probe and its sample cadence are unchanged.
The ordinary coarse resource stream retains its existing duration-based sampling
bound, so longer allowances can have coarser coarse samples; the year stream
still samples every two seconds. These are passive **sampled** peaks, with the
same measurement limitations as earlier trials, not zero-overhead instrumentation
or exact live-set measurements. Comparing two seeds can expose retained demand,
but does not prove the absence of leaks over ten repetitions.

**VM follow-up to prepare separately:** Gini-on first, ten repetitions through
2070 in one JVM. If resource failure requires different limits, restart the
qualification at the revised fixed limits; then run Gini-off with matching
limits/seeds/inputs/model. This laptop harness intentionally remains limited to
one/two repetitions and the calibration's 48-hour guard. Ten long Gini repetitions
may exceed that guard and require a separately reviewed longer-run evidence and
runtime budget. They must not be launched by simply changing this command to ten.

If Gini has substantial cost, retain separate supported output profiles or a
larger allowance, and consider a separately reviewed model-maintenance change
to its algorithm. This harness changes no scientific calculation code.

## First native pair — 20 October 2026

The one-round campaign at
[/tmp-codex/optional-gini-20261020-090546/report.json](/tmp-codex/optional-gini-20261020-090546/report.json)
passed. Both conditions used seed 606, identical prepared inputs, the same
immutable runtime and model JAR as the completed smaller-memory campaign,
2 vCPUs, a 2.5 GiB heap and a fixed 3.5 GiB container. They differed only in
`calculateGiniCoefficients`. The requested population was 20,000, with 18,883
people in the prepared starting population; all three annual years completed.

| Measurement | Gini off | Gini on |
| --- | ---: | ---: |
| Whole Java process, including setup and shutdown | 137.931 s | 265.041 s |
| Annual simulation/output work, excluding setup | 58.576 s | 182.146 s |
| Sampled heap peak | 1.861 GiB | 2.088 GiB |
| Sampled working-memory peak | 2.311 GiB | 2.661 GiB |
| Total charged-memory peak, including file cache | 3.500 GiB | 3.500 GiB |
| Cumulative GC collection counter | 7.427 s | 8.019 s |
| Retained output | 31.300 MiB | 31.297 MiB |

Whole-JVM time increased by **127.110 seconds (92.2%)**. Annual work increased by
**123.570 seconds (211.0%, or 3.11 times)**, with approximately 39–43 extra seconds
in each year. This is a substantial observed calculation cost; it is not a
statistical significance result from repeated pairs. Working memory increased by
0.350 GiB, but both conditions passed at their original fixed allowances, without
growth, retries or pressure events. The actual Gini columns were finite and
nonzero in each enabled year, and zero in the disabled case. Annual person-row
counts matched; unchanged microdata values are not claimed.

The campaign took **21 minutes 30 seconds** overall, including approximately
8 minutes 20 seconds of shared preparation and separate dependency/database/browser
setup and audits. Both native reports, outer cleanup reports and independently
reopened compact archives passed. Original containers and reservations were
removed/released, bulky outputs were disposed only after archival, and generated
inputs were retired. Production allocations remain unchanged.

This establishes successful Gini execution for this short, single-seed case,
not for 2070, ten repetitions, lower CPU quotas or larger populations. Its extra
runtime must not be treated as a constant across population sizes. Keep the
baseline lower-CPU/memory qualification separate from any Gini-enabled profile.

Readable results:
[comparison.html](/tmp-codex/optional-gini-20261020-090546/comparison.html).

## Harness verification

On 20 October, **108 local checks passed**, covering calibration, population
profiling, the paired Gini harness, smaller-memory guards and existing
benchmark/storage archival. The default plan passed the current disk/RAM
preflight and selected the same model JAR as the completed smaller-memory
campaign. The first native scientific pair is recorded above. The larger short
50,000-person pair below also passed; the long Gini sequence is still to run.
After fixing database export off for the online service, **106 configuration,
browser/YAML, resource-profile, calibration and paired-output checks passed**.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
~/simpaths-browser-tests/venv/bin/python -m unittest \
  deploy.multirun.test_research_calibration \
  deploy.multirun.test_research_population_profile \
  deploy.multirun.test_research_output_trials -v
```

These checks use fictional scientific launches with real small-file annual
auditing, archive verification and guarded disposal. They do not substitute for
the native Gini on/off comparison.

The two-repetition update passed **124 local checks** across the Gini harness,
calibration, population/year/memory reporting, benchmarks and storage audit.
They include one native-launch request carrying two seeds, rejection of invalid
Gini output in the second repetition before archival/disposal, explicit deadline
bounds and the normal earlier paired defaults. Scientific launches in those
checks are fictional. The exact 50,000/2070, two-repetition Gini-on CLI plan also
passed the current local disk/RAM preflight (19 GiB free disk / 7 GiB available
RAM required). The long scientific test itself remains to be run in the terminal;
neither measurement probe nor the scientific JAR was changed by this update.

## Short 50,000-person pair — 20 October 2026

[`optional-gini-50k-20261020-105818/report.json`](/tmp-codex/optional-gini-50k-20261020-105818/report.json)
passed both modes for 2019–2021, one seed 606, 2 vCPUs, fixed 3 GiB heap and 4 GiB
container. The Java lifetime was **235.562 seconds off / 1,171.227 seconds on**;
annual simulation/output work was **149.980 / 1,081.945 seconds**. Sampled heap
peaks were **2.211 / 2.463 GiB** and working RAM **2.679 / 2.959 GiB**. Whole-JVM
time increased by 397.2% (4.97 times), with 935.665 extra seconds. Gini values
were checked for all three years; annual exported person-row counts matched.
The complete campaign took 38 minutes 44 seconds including preparation/audits.
This is one short matched pair, not qualification of 2070 or ten repetitions.
