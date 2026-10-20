<!-- (C) Copyright 2026, by Ross Richardson
Matched scientific screening of optional Gini calculation costs.
@author ross richardson
-->

# Optional output calculation trials

Prepared on 20 October 2026. The completed smaller-memory campaign enabled all
standard annual CSV exports, including Household and the regular statistics
files. It disabled `calculateGiniCoefficients` and `exportToDatabase`. This small
follow-up changes **only the Gini flag**, with a fresh reference run in every
pair; it does not enable database output or change production resource profiles.

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
the Gini-on JVM took 4 minutes 25 seconds. Larger-population Gini times remain
unmeasured. Review the screening deadline before that follow-up: pairwise scaling
could make it exceed the current allowance without indicating a memory failure.

Each condition's bulk output is independently audited and hash-checked, compact
evidence is re-opened, and only then are its bulk files removed. The shared new
prepared inputs are retired only after every condition succeeds. A failed
condition, mismatched pair, unexpected Gini output or uncertain cleanup stops
the campaign and preserves the relevant evidence and remaining inputs.

Open `comparison.html` in the output directory for labelled tables;
`comparison.csv` and `report.json` retain the numeric observations, paired
differences and Gini validation. Original completion evidence and passive
year/workspace samples remain under `archives/`. Interpret only completed native
conditions; interrupted or failed conditions retain their own diagnostics.

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
campaign. The first native scientific pair is recorded above; the three-round
campaign and larger-population follow-up remain unmeasured.
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
