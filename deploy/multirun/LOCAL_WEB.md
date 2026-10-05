<!-- (C) Copyright 2026, by Ross Richardson
     Local MultiRun browser preview: approval, prepared examples and queued uploads.
     @author ross richardson -->

# Local MultiRun browser preview

This is a separate local application at **http://127.0.0.1:5002**. It uses the
tested PostgreSQL queue and container adapters. It does not start `app.py`, use
Redis, or replace the interactive SingleRun page. No image rebuild is needed.
For a persistent HTTPS deployment use [the native VM guide](VM.md), not this
console-code launcher. Both routes share the application setup in `runtime.py`.
For a read-only terminal overview of an existing service, see
[operator status](OPERATOR.md).
For matching PostgreSQL/private-file backups and isolated recovery, see
[backup and restore](BACKUP.md).
The page uses the same SimPaths logo and favicon as SingleRun, with the logo
beside **SimPaths Online**, with **UK MultiRun** beneath the title, including
before sign-in. The browser tab is titled **SimPaths Online — UK MultiRun**. The
assets are served locally by JAS-mine-web.

You can sign in, select prepared training inputs or upload and prepare your own,
create fixed configuration cards, duplicate them, select a baseline, review the
shared seed sequence and submit. **My jobs** shows durable status, attempt counts,
cancellation and automatic-retry controls. A browser refresh or closure does not
cancel submitted work.

Each page has a collapsed **Help with…** panel beside its controls: New Experiment,
Create Input Dataset, My Datasets, My Jobs, Storage and Results. The panels explain
inputs and model versions, configuration/repetition workload, parameter sweeps,
retry eligibility and cancellation, retention and deletion, Online Visualiser
comparisons, optional input downloads and resumable ZIPs. They refer to the
service's displayed allowances and dates rather than fixing values in the help.
The Results panel explains that current charts switch between alternatives and
that the local-folder picker still requires a separate Baseline/Scenario pair.
Restart the launcher and refresh the page after updating this guidance; no model
image or Visualiser build is required.

The **Default input dataset** supplies inputs to every card unless that card
selects an alternative. Each dataset contains its prepared population/donor database,
UKMOD policy schedule and parameter workbooks. To compare a different schedule,
workbook set or population, create or select a second dataset and choose it on the
comparison card. Different inputs must use the same model JAR version. Each card
has its own validated population/years; all cards share the seed sequence.

The **Model release** selector in Create Input Dataset defaults to the operator's
selected version and names retained older versions separately. Prepared datasets
keep their original model; choosing a newer default does not upgrade them. Dataset
selection supplies that model to new experiment settings. Explicit web YAML/copy
version pins survive refresh and are checked against the chosen inputs at review.
See [retained model releases](RELEASES.md) for registration, selection and safe
upgrades. No JAR rebuild or new simulation is needed merely to adopt the registry.

**Create input dataset** appears to the left of **New experiment**, which remains
the initially visible page. Give uploaded inputs a name before preparation. The
name is retained with the published dataset; selectors refresh automatically, including datasets still being prepared, while
preserving current choices. Previously prepared datasets
without names retain their generated descriptions. Dataset names are labels, not
content identities: two datasets can have the same name, with their IDs distinguishing
them. A disappearing permission does not silently switch a selected dataset.

The configuration cards show all model parameters supported by this web profile,
in their `SimPathsModel` declaration order, with no special prominence for saving
rate. This includes parameters without `@GUIparameter`; that annotation controls
the interactive GUI, not native MultiRun YAML assignment. The supported list is
explicit in `schema.py`, with presentation metadata in `browser_model.py`.
Population, years and the shared seed plan are configured separately. Advanced
capabilities and output settings not exposed in this browser remain controlled by
the profile; the native launcher's ability to assign a field does not establish
that it is suitable for this web workflow. YAML import/export is connected to the
browser form; a sweep helper generates fixed configurations in compact summary cards.

This preview defaults to a maximum of 100 configurations per experiment. The
operator can choose a lower limit with `--max-configurations`. Repetitions default to a maximum of 3
per configuration; the operator can set `--max-repetitions` when starting the
service. Each configuration uses the same seed sequence, starting at 606 by default and
increasing by one per repetition. The local pool admits one job at a time, with two CPUs and up to
5 GiB container memory. Each simulation attempt is allowed 15 minutes for setup
plus 60 minutes per planned repetition, with a cumulative execution budget of
three times that allowance across at most three attempts. The operator can
configure these values as described below. Its allowance does not coordinate with the separate
SingleRun server: finish other simulation sessions before this local test.

### Importing and exporting experiment settings

Open **Import or export YAML settings** at the top of New Experiment. **Export
YAML** saves the current validated settings. Choose a `.yaml` or `.yml` file and
press **Import YAML** to validate it, then confirm replacement of the current draft.
Importing does not create datasets or submit jobs. Invalid imports and cancelled
confirmations leave the draft unchanged. Review the populated form before submitting.

The web format `simpaths.multirun.web.v1` contains `configuration` (the existing
versioned SimPaths contract), `baseline` and `auto_retry`. It preserves names,
configuration order/IDs, repetitions, the first seed, model and collector settings,
the default dataset and per-configuration dataset/year/population overrides. The
first seed is a decimal string, preserving all signed 64-bit values through the
browser. Every repetition adds one, with overflow rejected. **Output and statistics
settings** shows collector controls; required annual comparison outputs are fixed.
**Use different population or years** allows a card to override common settings
while inheriting the default dataset, subject to that dataset's locked fields.

The browser also accepts a plain `simpaths.multirun.v1-draft` configuration, or the
supported native `SimPathsMultiRun` YAML subset described in [README.md](README.md).
Native imports create one card using the currently selected default dataset and
experiment name. Plain/native imports default to no baseline and automatic retries.
Omitted supported settings use the model defaults shown in the form. Unknown or
managed execution fields, incompatible output settings, unsupported capabilities,
unavailable model releases and work exceeding deployment limits are rejected.
Native files requiring population/donor preparation must first use Create Input
Dataset; importing YAML does not perform that preparation.

Files must be UTF-8 and at most 1 MiB. Duplicate keys, tags, anchors/aliases, merge
keys, excessive nesting, multiple documents and non-finite numbers are rejected by
the existing bounded parser. YAML exports contain fixed configuration cards and
any saved sweep origin. Direct sweep recipes are not accepted by YAML import;
use the browser helper to generate cards first. Input workbooks, populations and UKMOD files
are not included in a settings export. Dataset references grant no access: a missing,
expired or unauthorised dataset remains an unavailable selection that the user must
replace before submission. Known locked population/year values must agree with the
dataset. Pending authorised inputs are accepted. Import/export does not refresh
retention or create queue work. Submission rechecks permissions and input identity.

Refresh and Duplicate retain imported collector settings, seed choices and stable
IDs. Reset restores defaults while retaining the chosen default input dataset.
There is no database migration or model image rebuild for this feature; restart
the local launcher after updating both repositories.

Acceptance: 65 local model/configuration/submission/container tests pass, including
8 YAML-specific tests. The laptop run `multirun-browser-20260929-000018` passed
10 HTTP/database tests and 43 browser checks; temporary test resources were removed.

### Generating configurations from a parameter sweep

Below the cards in **New Experiment**, open **Generate configurations from a
parameter sweep**. Choose a starting card and add one or more supported model
parameters. Enter comma-separated values, or choose **Start, end and step** for a
numeric range. Boolean values are `true` and `false`. Ranges include both ends;
the end must be reached exactly by whole steps. Descending ranges need a negative
step. Repeated values, unsupported fields and invalid combinations are rejected.

**Preview sweep** lists every combination and the number of configurations and
simulations the experiment would contain. Existing cards are kept. A combination
with the same effective dataset, population/years, model and collector settings
as an existing card is explicitly shown as **Use existing configuration** and is not added
again. For example, starting with saving rate 0.04 and trying 0.04, 0.05 and 0.06
adds two cards; with three repetitions each the experiment contains nine
simulations. Multiple parameters use all combinations, not positional pairing.

**Add generated configurations** appends the new configurations after this preview,
presented together in a **Parameter Sweep** summary card. The
starting card's dataset choice (including inheritance), common overrides and
output settings are copied. Existing cards and the baseline stay unchanged.
All cards use the experiment's common repetition count and seed sequence.
The deployment's configurable limit (100 by default) includes existing cards; oversized
products are rejected before expansion. Seeds, execution controls, input datasets,
population/years and collector settings are not sweep dimensions. They can still
be set through the existing form controls where supported.

The summary shows the starting configuration and inputs, swept values,
configuration count, repetitions and total simulations. **Show configurations**
expands a table of combinations and their current values. **Edit** opens one
configuration's full settings; **Back to combinations** returns to the table.
Edited combinations are marked **Edited**, including changes outside the swept
parameters. The original sweep dimensions remain creation history. Existing
matching cards stay in their original positions, and grouping never changes
configuration IDs, submission order or the baseline.

For example, three parameters with three values produce 27 combinations. If none
matches the starting card, the experiment contains that card plus 27 generated
configurations, all displayed through one sweep summary. Matching combinations
reuse existing configurations rather than adding duplicates. Independent sweeps
have separate summaries.

Refresh preserves the configurations, expanded tables, open editor and helper
values; Reset clears the summaries/helper and restores the usual single default card.
Changing settings after preview requires another preview. **Parameter sweep
origin** records the starting settings and generated values; it is creation
history, so later edits may make the current values different. YAML export/import,
Duplicate and submission preserve this bounded `simpaths.sweep.v1` metadata.
Workers and retries execute the frozen current card values, never the recipe.

The preview endpoint checks session, CSRF, dataset access and locked input fields.
Pending authorised datasets are accepted. Previewing and generating draft cards
create no jobs, uploads or retention extensions. Normal review and submission
perform their existing checks. No database migration or model image rebuild is
needed; restart the launcher to load both repository changes.

Validation: 74 local configuration/model/submission/container tests pass, including
nine sweep-specific cases. The laptop run `multirun-browser-20260929-004139` passed
all 12 HTTP/database tests and all 44 browser checks, including sweep generation,
refresh and YAML. Temporary test resources were removed. The desktop and narrow
screenshots were also inspected. This was a focused backend suite, not a full
queue/Docker rerun.

### Copying a submitted experiment

In **My Jobs**, choose **Copy to New Experiment** beneath an experiment heading.
Confirm replacement of the current draft; declining leaves the draft unchanged.
The page opens New Experiment with the source name followed by `(copy)`. Edit the
settings and use the normal review/submission steps. Copying itself creates no
jobs, uploads, input copies or retention extensions, and leaves the original
experiment and its results unchanged. Preparation jobs do not offer this action.

The copy preserves configuration names/order, model and collector settings,
baseline, repetitions, exact first seed and sweep origin. New submissions retain
validated form settings in the queue specification, so dataset inheritance and
common overrides can be restored exactly. Older submissions are reconstructed
from their per-configuration effective settings; the first configuration supplies
the form defaults. Any approved input replacements made after submission are
included. Older retry preferences use the current job controls, with retries off
if any configuration has opted out; new submissions preserve the submitted choice.

An experiment can be copied while queued/running or after completion, failure,
cancellation or deletion of its output files. The service checks ownership and
current input permissions. Missing/deleted/expired or inaccessible inputs remain
marked as unavailable and must be replaced before submitting. Owned pending
inputs are accepted. If the repetition allowance was lowered, the saved number
is shown with a request to reduce it; it is not silently changed. An unavailable
model release or unsupported saved settings produce an error without replacing
the draft. Input compatibility and execution permissions are checked again at
review and submission.

The new draft, including unavailable input references, survives refresh and can
be exported as YAML. Results, previous attempts and cancellation/failure state
are not copied into new jobs. No model image rebuild or database migration is
needed; restart the launcher after updating both repositories.

Validation: 83 focused local model/configuration/submission/container tests pass,
including nine copy-specific cases. All 16 HTTP/database tests passed in
`multirun-browser-20260929-045717`, and all 46 browser checks passed in the
browser-only rerun `multirun-browser-20260929-050150` after a test assertion fix.
Temporary test resources were cleaned up; no real simulations or emails were used.

### Retrying a failed configuration in its original experiment

In **My Jobs**, a simulation configuration marked **needs review** has a
**Review retry** button. The review shows the configuration, input reference,
saved seed sequence, previous outcome, attempts used and remaining time allowance.
**Confirm retry** queues that configuration again from the beginning, keeping the
same settings and seeds. Completed configurations, the baseline, previous attempt
history and retained results remain in the original experiment. Nothing is
submitted by opening the review, going Back or refreshing the page. **Cancel**
still means cancel, never retry. Preparation jobs use their existing controls.

Retries still use normal queue admission and fresh private working directories.
They share the original three-attempt maximum, per-attempt deadline and total
time allowance. The review explains when access, deleted inputs, unfinished-work
allowances, cancellation/expiry, attempts or time budgets prevent a retry.
Permissions, inputs and budgets are checked again on confirmation. A retry does
not correct model settings or increase memory/storage allowances; an unchanged
model/resource failure may recur.

**Local laptop recovery:** the loopback launcher explicitly enables an optional
**Exclude time recorded after the previous deadline** checkbox for a finished
time-limit failure. It starts unchecked. The review identifies the precise delay
and the resulting budget; confirmation records a bounded adjustment in PostgreSQL.
This accounts for late reconciliation after sleep or service interruption without
claiming to measure CPU time. Time before the original deadline still counts,
all attempts still count, and neither the next attempt limit nor total configured
allowance is increased. Raw elapsed times and failure records are preserved.
An adjustment cannot be granted twice for the same recorded interval. Ordinary
automatic retries do not grant adjustments or restart time-limit failures.

The generic service disables this local option by default. Production operators
must make an explicit decision before enabling it; it does not imply that a VM
will sleep or that all delayed reconciliation should be credited. Keep the laptop
awake during simulations. If the pre-deadline budget or attempts are genuinely
exhausted, copying to a new experiment remains available where inputs permit.

Restart the launcher to apply migration 018 and load the controls, then refresh
the browser. No image rebuild or automatic restart of failed jobs occurs. The
migration preserves existing time charges and grants no retrospective adjustments.
Acceptance passed in `multirun-browser-20260929-101651`: all 277 backend tests
and all 47 browser checks passed. Temporary test resources were cleaned up.
The browser proof confirmed a retry after the explicit local delay adjustment,
preserving the completed baseline and original experiment's Results page.

## Viewing and downloading completed results

Open **My jobs → View results** for an experiment. Its results link can be
bookmarked and reopened after signing in. Completed configurations remain
available separately if another configuration fails or is still running.

For a configuration using your supplied inputs, **Download CSV output (.zip)**
downloads the explicitly supported scientific CSVs for each repetition. Provider
datasets, including the supplied Quick Start examples, do not permit detailed
output downloads. The service checks current ownership and permissions on every
request; there is no access granted just by knowing the URL.

An **Include input dataset** checkbox beside each individual/comparison download
adds the retained inputs supplied by that owner. It is unchecked initially. A
comparison includes each distinct dataset once, even when both configurations use
it. The ZIP keeps the existing CSV folder layout. A single configuration, or a
comparison using the same dataset ID for both configurations, has one `Inputs`
folder; the manifest maps configurations to their dataset copies:

```text
comparison-<baseline>-vs-<scenario>-<reference>-with-inputs/
├── manifest.json
├── Inputs/
│   ├── input.mv.db
│   ├── tax_donor_population_UK.csv
│   ├── DatabaseCountryYear.xlsx
│   ├── EUROMODpolicySchedule.xlsx
│   ├── <parameter and scenario workbooks>
│   ├── InitialPopulations/<uploaded population CSV>
│   └── EUROMODoutput/<uploaded UKMOD files>
├── Baseline/
│   └── run_1/
│       ├── input/options.txt
│       └── csv/...
└── Scenario/
    └── run_1/
        ├── input/options.txt
        └── csv/...
```

Further repetitions use `run_2`, etc. A comparison using different datasets has
`Inputs_Baseline` and `Inputs_Scenario` instead of `Inputs`, with the same internal
file structure shown above. The workbooks and database sit directly in each input
folder, with no extra `dataset_1/input` layer. The `input_folder` in each manifest
configuration identifies the right copy. Bundled filenames end in `-with-inputs.zip` and have a different
reference from output-only ZIPs. The first configuration in a comparison filename
remains the baseline.

There is one native **`options.txt` per repetition**, written during model setup.
Cleanup already preserves it when removing repeated native input snapshots.
Downloads now include it under each run's `input` directory even when the checkbox
is unticked. The manifest additionally records the full frozen normalised and
native configuration, seeds, model/prepared-input fingerprints and job/attempt
references: some supported parameters are absent from native `options.txt`.
These settings files follow output retention and are removed with an explicit
output deletion; they are not kept indefinitely.

The optional inputs are the sealed prepared dataset used to initialise runs,
including the selected source files and generated population/donor data. They
are not copies of a database subsequently modified during each run. Together with
the recorded settings they document the input/output relationship; this is not an
automatic replay facility or a claim that the known SimPaths reproducibility
issue has been fixed. Model JARs, logs and database diagnostic/lock files are not
included. The export does not enable uploading a prepared H2 database back into
the service: future reuse still requires the accepted source-file validation flow.

Input bundling is available only while the input dataset is retained. Dataset
deletion/expiry disables this option, while output-only downloads remain usable.
Downloading does not reset the seven-day input retention clock. An active input
download holds off physical dataset removal until it ends or the server process
exits; cleanup can then resume. New downloads cannot start once deletion is requested. Provider-derived
inputs remain blocked even when an owner has edited public parameter workbooks.

With two permitted completed configurations, **Download a comparison** creates a
ZIP containing a folder named after the ZIP filename without `.zip`. Within it
are `Baseline/run_1/csv` and `Scenario/run_1/csv`, then `run_2`, etc., alongside
`manifest.json`. Individual configuration downloads use the same outer-folder rule.

Comparison filenames follow `comparison-<baseline name>-vs-<scenario name>-<reference>.zip`:
the first configuration is always the baseline and the second is the scenario
selected for that download. For example,
`comparison-Configuration-1-vs-Configuration-2-cd2731ebb6af.zip` uses Configuration 1
as Baseline and Configuration 2 as Scenario. The outer folder has the same name
without `.zip`. Names are shortened and made safe for filenames; `manifest.json`
preserves the full configuration names and explicitly records each `Baseline` or
`Scenario` role. Individual downloads use `results-<configuration name>-<reference>.zip`.

The configured experiment baseline is selected initially when available; download
selections do not change it. Every run includes `Person.csv` and `BenefitUnit.csv`.
Other supported exports are Household, WealthIncomeStatistics,
DemographicStatistics, AlignmentStatistics, LabourStatistics, HealthStatistics and
WellbeingByGender CSVs when produced. The `manifest.json` file records configuration
names, each folder's random seed and checksums. Files keep their original bytes.

For several alternatives, tick **Include several alternatives** in the download
panel and select the required configurations. The baseline is excluded from the
alternative choices. The ZIP contains `Baseline/run_X` and `Scenario_1/run_X`,
`Scenario_2/run_X`, etc. Its `jasmine-results-v2` manifest records the experiment
name, explicit baseline/alternative configuration IDs, full names, each output
folder, frozen settings, seeds and checksums. Every repetition retains its
`input/options.txt`. A baseline with more than one alternative uses a bounded
filename such as `comparison-Configuration-1-vs-2-scenarios-<reference>.zip`;
the manifest keeps the complete names.

**Include input dataset** adds each distinct retained owner-supplied dataset once.
If all configurations use the same ID, the input folder is `Inputs`. Otherwise,
its first occurrence determines `Inputs_Baseline` or `Inputs_Scenario_N`; every
configuration's `input_folder` points to the right copy. Thus two alternatives
sharing one dataset reuse that folder, even when their baseline uses another.
Every selected configuration must permit detailed downloads, including an
alternative placed last in the list. A missing/revoked/deleted source prevents
starting or resuming the set; the service does not silently omit it. Large ZIPs
use the same compressed, verified, resumable preparation described below.

Extract a single/pair ZIP and select its named folder containing Baseline and/or Scenario
in the Policy Impact Visualiser's **Visualise Your Own Data** picker. Larger
experiments can still download separate comparison pairs. The current local folder
scanner accepts only those two role names; numbered multiple-alternative ZIPs need
future Visualiser picker support. The authenticated Online connection already
supplies all selected aggregates and lets users switch the displayed alternative;
see [the connection guide](VISUALISER.md). Restricted
provider results require server-side aggregation with approved output controls,
so they cannot use this local raw-CSV route.

For container runs, the queue's `model_digest` identifies the Docker image. Input
export checks it against the prepared receipt's `source_image`, not the separate
Java JAR checksum. The prepared-dataset fingerprint binds that JAR checksum and
the input-file inventory. Confusing the two identities rejects otherwise valid
downloads even though output-only downloads still work.

Existing retained outputs are checked against their recorded successful-attempt
fingerprints; the original prepared dataset need not still exist. No model rebuild,
new simulation or database migration is needed. Restart the launcher with the
matching updated JAS-mine-web checkout. Small downloads stream directly; large
downloads use the resumable compressed archives described below. Missing or changed
source files are refused rather than regenerated.
Raw logs are excluded; prepared input databases require the explicit permitted
input-bundling option above. Retention dates and notices are recorded,
but automatic deletion and email delivery remain disabled by default locally.

## Resumable large downloads

Results-page downloads below 512 MiB of selected files keep the direct streaming
path. Larger selections, including output-only selections, prepare a compressed
ZIP64 archive in the background and show progress on the page. Refreshing the
page restores preparation status. Ready downloads show their actual compressed
size and availability date, and support HTTP byte-range resumption in compatible
browsers/download clients. Prepared archives remain usable after a server restart;
source access and expiry are checked again on every transfer or resume.

The launcher accepts these optional deployment flags:

- `--download-threshold-mib 512`: minimum selected, uncompressed file size for
  preparation. Inputs shared by comparison configurations count once.
- `--download-cache-gib 10`: maximum combined temporary archive storage. This
  caps actual compressed bytes and does not allocate/reserve that much disk.
- `--download-cache-hours 24`: archive lifetime from completion, without changing
  the original input/output retention dates.

No launch-command change is needed to use these defaults. For a quick manual test
with small outputs, use `--download-threshold-mib 1`; this does not change simulation
settings or existing results. One archive builder runs at a time, with a 1 GiB
free-space reserve. Low disk space or an exhausted cache fails download preparation
with a visible explanation and removes the partial ZIP. Source files stay intact.
The cache occupies `<state>/execution/download-cache`; it is private, automatically
cleaned while the web service runs, and included in the owner's Storage summary.
Do not delete active cache files by hand. It uses shared VM disk, independently
of the retained-upload allowance.

Large files use ordinary DEFLATE compression at level 1. Verification is combined
with writing the compressed archive after model-specific catalogue validation.
CSV/text may shrink substantially; compressed or poorly compressible inputs may
not. There is no scientific change to extracted files. The existing outer folder,
Baseline/Scenario roles, input folders and per-run `options.txt` remain unchanged.
The same owner, approval and user-only source restrictions apply to the cached
copy. Dataset/output deletion blocks new transfers; active transfers retain their
file guards until they finish. Cache expiry needs no separate scientific-data
warning because a download copy can be recreated while its sources remain.

A prepared download can resume only while its cache entry, sources and user access
remain valid. Re-sign in if necessary. The Results page can prepare another copy
when an old one expires; the version in a saved URL never silently changes.
The old direct `/downloads/` URLs still work but are not resumable. Use the Results
page to request the new preparation path. Network interruptions can still happen;
resumption provides recovery rather than guaranteeing uninterrupted connections.
This implementation is for the VM service; it adds no Cloud Run deployment path.

## Prerequisites

- The JAS-mine-web checkout containing `jasmine_web.batch.browser`.
- Docker, the already installed `postgres:17-alpine` image and the validated
  `simpaths-interactive:uk-user-data` image. The launcher does not pull images.
- Java/Javac 25 for input preparation; the current built `multirun.jar`.
- A Python environment with the optional dependencies below and this model's
  `deploy/multirun/requirements.txt`.
- Space for one active job's input copies and retained results. The existing
  preparation and run-space guards still apply. The local launcher removes
  completed input copies, but retains scientific outputs and private diagnostics.

Install into your chosen environment, using the location of your frontend:

```bash
python -m pip install \
  -r /path/to/JAS-mine-web/requirements-batch-web.txt \
  -r deploy/multirun/requirements.txt
```

## Approve a local test identity

From the SimPaths repository:

```bash
python -m deploy.multirun.local_web approve --frontend /path/to/JAS-mine-web
```

The command prompts for an email address. Approval lasts 30 days; repeating this
explicit command renews it. `revoke` instead of `approve` removes access and
invalidates existing sessions. Neither command sends email.

## Start the page and worker

Use the **verified imports** created by `deploy.multirun.import_quickstart`, not
Docker build contexts. These directories are verified and referenced in place;
keep them available for later runs.

```bash
python -m deploy.multirun.local_web serve \
  --frontend /path/to/JAS-mine-web \
  --console-codes \
  --max-repetitions 3 \
  --prepared /path/to/prepared-20000 \
  --prepared /path/to/prepared-50000
```

The `--prepared` arguments are optional and remembered for subsequent starts.
Only these explicitly imported public training examples are granted to approved
local identities. Uploaded datasets remain owner-specific. The worker and browser
server run in this terminal; leave it open while testing.

### Configure the repetition limit

Set `--max-repetitions 10`, for example, to allow users to request up to ten
repetitions per configuration. The operator setting accepts integers from 1 to
1,000 and defaults to 3 if omitted. Supply it on each launch; it is not saved in
the state directory. Invalid values are rejected before connecting to PostgreSQL
or Docker. The technical ceiling of 1,000 is not a measured production allocation.

The same setting supplies the form's maximum, the New experiment description,
and server-side validation of new submissions. It applies to prepared training
data, prepared uploaded data and datasets awaiting preparation. The form initially
selects three repetitions, or the configured maximum if smaller. Restored drafts
keep their previously entered count and must satisfy the current limit on review.

Changing the setting requires a service restart and page reload. Lowering it
restricts new submissions; accepted jobs keep their frozen seed plans through
preparation, execution and recovery. Repetitions within a configuration remain
sequential. The attempt allowance and cumulative execution budget scale with
the number of repetitions requested, using the runtime settings below. Increasing
the permitted count does not raise concurrency, RAM or pool capacity. New-release
working storage scales with the requested count as described below; existing
fixed-policy datasets/jobs retain their recorded allowances.
Choose production settings after measuring representative workloads. This option
requires no image rebuild.

### Configure configuration working-storage allowances

Newly registered releases default to **4 GiB fixed + 256 MiB per repetition**,
with a larger fixed term when the selected input dataset's working copies need it.
Three repetitions normally reserve 4.75 GiB; twelve reserve 7 GiB. The review page
shows the calculated per-attempt allowance and flags pending inputs as provisional
until their prepared database size is known. A configuration exceeding the service's
configured pool capacity fails review; fitting jobs wait while capacity is busy.

Storage settings belong to the immutable release's resource policy. Restarting
an existing service keeps its retained policies, including historical fixed 10 GiB
allocations and 512 MiB-per-repetition allowances. Register/select a compatible new
release to opt new inputs into the 256 MiB term;
policy-only changes require no image rebuild. See [RELEASES.md](RELEASES.md#resource-policy-interface)
for policy JSON, old-job protection, pending-size adjustments and the full 50,000-person
2019–2026 measurement command. Retained datasets/results and download caches need
their own space. Working reservations/monitoring do not enforce hard disk quotas.

The large native input snapshot is made only on the first repetition. Successful
cleanup removes it and the private working input copy after confirmed container
removal, keeping CSVs, logs and **each repetition's `options.txt`**.

### Configure configuration runtime allowances

The launcher derives one overall limit for each simulation configuration attempt:

```text
attempt allowance = setup allowance + repetitions × allowance per repetition
cumulative execution budget = attempt allowance × budget multiplier
```

It covers workspace setup and all sequential repetitions together. There are no
separate repetition timers. The cumulative budget belongs to each configuration,
independently of how many other configurations the experiment contains. Queue
waiting, input-preparation waiting and time awaiting user review do not consume
it. A retry runs the configuration again from the beginning with the same seeds,
and receives the smaller of its original attempt allowance and its remaining
cumulative budget. The maximum remains three attempts, including the first.

Operator options, supplied on each launch:

| Option | Default | Accepted values |
| --- | --- | --- |
| `--runtime-setup-minutes` | 15 | Integer minutes, 0–1,440 |
| `--runtime-per-repetition-minutes` | 60 | Integer minutes, 1–1,440 |
| `--runtime-budget-multiplier` | 3 | Integer multiplier, 1–3 |

For example, add these flags to the normal `serve` command:

```bash
  --runtime-setup-minutes 15 \
  --runtime-per-repetition-minutes 60 \
  --runtime-budget-multiplier 3
```

With these defaults:

| Repetitions in each configuration | Maximum per attempt | Cumulative budget per configuration |
| --- | --- | --- |
| 1 | 1 hour 15 minutes | 3 hours 45 minutes |
| 3 | 3 hours 15 minutes | 9 hours 45 minutes |
| 12 | 12 hours 15 minutes | 36 hours 45 minutes |

These are initial operator settings, to calibrate using representative population
sizes, simulation horizons and setup costs. Review shows the formula and both
calculated limits before submission; retry review shows the frozen limits and
remaining budget. YAML stores scientific settings and repetition counts, so a
new import or **Copy to New Experiment** receives the current operator allowances
when reviewed.

Reviewed allowances are signed and stored with the accepted experiment. A
restart or changed runtime settings affect future reviews; accepted jobs and
still-valid signed reviews retain their original allowances. Older jobs retain
their previously frozen fixed policy. Input preparation retains its existing
one-hour attempt limit and three-hour cumulative budget. The local reviewed
deadline-delay adjustment remains bounded by the original saved policy.

The generic queue has technical bounds of 90 days per attempt and 270 days
cumulatively. These permit, for example, 1,000 repetitions at the default rate
(41 days 16 hours 15 minutes per attempt); the operator's repetition cap controls
whether such a request is allowed. Startup checks the formula at the configured
maximum repetition count before touching local state or starting PostgreSQL.
An incompatible setting is rejected, and the allowance is never silently
truncated. No database migration or model image rebuild is needed; restart the
launcher to load the code and settings.

Validation: `multirun-browser-20261001-104331` passed all 307 backend tests and
all 48 browser checks, with temporary-resource cleanup confirmed. This includes
reviewed formulas, independent configuration budgets, preparation/legacy limits,
restart preservation, queue/review waiting and bounded retries. Eight generic
policy tests, 88 local SimPaths tests and the DOM review checks also passed.

### Configure configuration and queue admission limits

`--max-configurations` accepts 1–100 and defaults to 100. It controls manual entry,
sweep expansion and new YAML submissions. The same limit appears in the form and
server validation; the parser and generic queue retain a technical ceiling of
100 configurations and 10,000 total simulations per experiment.

Queue admission has separate settings: `--max-unfinished-jobs` defaults to 110
per user and `--pool-unfinished-jobs` defaults to 220 across the local service.
Configurations and input preparations both count. The defaults allow one full
100-configuration experiment plus some preparation/other work per user. The
required relationship is:

```
max-configurations <= max-unfinished-jobs <= pool-unfinished-jobs <= 10000
```

Supply these settings on each launch. Startup explicitly updates only these two
queue admission allowances in an existing local pool. CPU, memory, disk budgets,
one-job execution concurrency and time/retry limits stay fixed. Already accepted
experiments retain their frozen policies and configuration order; lowering
admission does not cancel them. A full queue rejects a new submission atomically.

Previously saved drafts and copied experiments with up to 100 configurations remain
editable if the operator lowers the current cap. The user must remove enough
configurations before submitting again; configurations are never silently dropped.
YAML imports/exports must satisfy the current cap. These changes require a launcher
restart and page reload, with no database migration or model image rebuild.

Acceptance on 1 October 2026 verified 100-configuration HTTP submission, YAML and
copying, atomic queue admission and preservation of accepted execution policies.
The 297 backend cases passed across full run `multirun-browser-20261001-092156`
and focused Visualiser rerun `multirun-browser-20261001-093851`, after updating an
old test fixture for the larger specification bound. Browser-only run
`multirun-browser-20261001-100254` passed all 47 checks, including a compact
27-combination sweep, individual editing, refresh, YAML, narrow layout and absence
of uncaught JavaScript errors. Both final reports confirm success and temporary
resource cleanup; the final run explicitly skipped backend tests.

### Configure the upload allowance

The default allowance is **2 GiB of retained source uploads per user**. If the
page reports **Upload storage allowance is full**, restart with
`--upload-allowance-gib 4`, for example, to raise that allowance to 4 GiB. Supply
the option on each launch; it is not stored in the state directory. It accepts
positive whole GiB values that fit the service's 64-bit byte counter; invalid
values are rejected before connecting to PostgreSQL or Docker. The server prints
the selected allowance at startup. No image rebuild or database reset is needed.

Keep the same `--state` directory (by default `~/simpaths-multirun-local`) to
retain approvals, uploads, prepared datasets and submitted jobs. Raising this
allowance does not allocate storage, free disk space, or change the 512 MiB
per-file limit, 100-file allowance or preparation/run space checks. Uploads still
leave at least 1 GiB free on their filesystem. Check free space where the state
directory lives; `/home` and `/` may be different filesystems. Lowering the
allowance does not delete existing uploads, but can prevent further uploads.

In **Create Input Dataset**, unticking excludes a file from the next preparation;
it does not delete it. Follow **open Uploads in Storage** to permanently remove an
unused upload using **Delete upload** and its confirmation. Storage is the only
place offering permanent deletion. Referenced sources stay listed, stored and
counted and can be selected again, including after a prepared dataset is deleted.
On restart, migration 009 restores previously cleared sources
where no current copy of that filename is listed; the latest retained ready version
is restored without changing the sources used by existing jobs. Retention releases
historical source references after their last dataset or unfinished job no longer
needs them; see the expiry policy below.
Reuse existing selected population/UKMOD files when creating a dataset with a
different replacement workbook. Choosing a file whose name is already listed
compares its contents using a server-calculated SHA-256 and byte count. Identical
files are selected for reuse without another stored copy, even at full allowance.
Changed files require confirmation; existing datasets and submitted preparations
keep their original versions. The comparison transfers the chosen file to the
server without saving it; a confirmed replacement is then uploaded and remains
subject to the storage allowance and free-space checks.
The list shows **Uploaded:** with each file's upload date/time and **Replaces an
earlier upload** for a confirmed replacement. A workbook uploaded then edited in
the browser keeps its upload date and adds **Updated:** for the latest saved edit.
Reusing identical contents changes neither date. A copy edited directly from a
model original has only an update date. Times use the browser's local timezone.
Do not remove files directly from the upload
directory: doing so leaves their database records and references behind.

**Console codes are an explicit local test mode.** Request a code on the page,
read it in this terminal, and enter it in the browser. This exercises the same
approval/session checks but does not prove access to an email inbox. The app
binds only to loopback. Do not expose it using a tunnel or reverse proxy.

## Suggested manual check

1. Sign in and choose the prepared 20,000-person training dataset. Its saved
   population and first year are fixed; leave the final year at 2020.
2. Leave the first configuration's saving rate at its default. Duplicate it,
   rename the copy, and change its saving rate. Duplicate cards must differ in
   at least one effective setting or input dataset before submission. Alternatively,
   select the 50,000-person training dataset on the copy; its saved population
   becomes 50,000 while the first card still inherits the 20,000-person default.
3. Select the first configuration as the baseline. Review the total simulation
   count, seeds, population/years, input identities and highlighted model differences.
   File labels compare with the baseline, or name matching configurations when no
   baseline is selected. Expand **Compare input files** above the settings table
   for the size/checksum explanation and individual changed or missing files.
   These checks identify changed files, not changed cells or database records.
   The schedule row compares the workbook; identical schedules can still refer
   to different donor data in the separately compared prepared database. Pending
   generated files say **Awaiting preparation**, with selected sources listed
   separately. You can submit immediately and wait for preparation, or wait and
   review again to compare generated files before submitting.
4. Open **My jobs**, wait for automatic updates, then close and reopen the page. Confirm that the
   same jobs remain visible. Cancellation and retry controls apply to one
   configuration, not every configuration in the experiment.
5. For the upload path, choose **Create input dataset**, enter a name, then upload a population CSV and
   UKMOD files, check the policy schedule (including a policy starting in 2015),
   then review and click **Create input dataset**. It queues preparation immediately.
   Select the named dataset in **New experiment** while it is still preparing;
   submitted configurations wait for validated inputs before becoming runnable.
   Prepared uploads can be reused in later experiments without preparing again.
6. Open **My Datasets** to see your created datasets and their sizes in MiB. Cancel
   a pending preparation through its impact review. To delete a ready dataset,
   follow **View in Storage** to its entry, then **Delete** and confirm the review.
   Choose replacement inputs for never-started dependent configurations
   or cancel those configurations. Running/retrying configurations retain their inputs;
   requested deletion waits until they finish or are cancelled. Source uploads,
   existing results and job history are kept separately.

Parameter workbooks must be declared model inputs. This page does not accept
input database files or archives. The native SimPaths validators still decide
whether a selection is usable. Keep local tests to public example data until
production storage controls and retention are completed.

## Waiting, replacement and deletion

Preparation runs even if no experiment depends on it. Waiting configurations retain
submission age and reserve no execution CPU/RAM. Once preparation succeeds, their
settings, seeds, input identity and resources are validated again. Normal queue
fairness and capacity determine when they start; readiness does not guarantee the
next slot. Failure blocks only configurations using those inputs.

Input replacement requires a review and is allowed only before a configuration has
started. It retains the original parameters, seeds and queue age, and records the
change. Incompatible years, population requirements or model versions are rejected.
Started work and its retries keep fixed inputs. A changed job or preparation status
invalidates an open action review; review again to see the current consequences.

Requested deletion is shown as **Awaiting deletion** while references remain.
The worker removes unreferenced prepared files from managed storage and recovers
interrupted removal on restart. Provider examples are excluded from **My datasets**.
The links at the top of Storage jump to Uploads, Prepared Input Datasets and Output
Files. Input selection and My Datasets link to Storage rather than duplicate its
permanent-deletion controls. Unticking alone keeps the uploaded copy available.
Seven-day expiry and 96-hour warning notices are described below. Automatic deletion
and mail delivery are separate opt-ins on the laptop.

## Persistence and restart

The default private state directory is `~/simpaths-multirun-local`; use `--state`
consistently to choose another location. It stores the stable session secret,
database credential, a frozen model/workbook snapshot, private uploads, prepared
artifacts, execution receipts and scientific outputs. PostgreSQL uses its own
labelled named Docker volume, with a loopback-only port. Nothing is broadly pruned.
The launcher prints its database container name; it can be stopped after testing,
and the launcher starts it again next time.

Ctrl+C stops the browser server and dispatcher. Already launched containers keep
their independent deadlines; queued work resumes after the launcher restarts.
Restart after updating both repositories to apply migration 010 (and earlier
migrations when needed). It preserves existing jobs, registers owned prepared
inputs for management and gives unfinished preparation a selectable dataset ID.
Migration 008 records which uploaded workbooks were edited in the browser.
Migration 010 distinguishes upload and edit dates. Older edited copies show their
known update date; the old schema did not preserve their original upload date.
Do not run an older dispatcher against the upgraded schema. Stop the local
launcher before updating, then restart it normally; do not delete its database.
Recovery may wait for the previous lease to expire (up to about one minute).
Stopping the frontend is therefore not the way to cancel a job: use **Cancel**.

Within an experiment, the order on **New experiment** is the preferred starting
order when configurations are ready and fit the available resources. The baseline
is used for comparisons and does not change queue priority. Waiting configurations
keep their original position while other ready work may proceed; retries still
observe their backoff. User fairness and resource limits continue to apply, and
available capacity can run configurations in parallel. Completion order is not
guaranteed. The dispatcher rechecks waiting inputs after publishing preparation
results and before claiming further work, so a just-prepared first configuration
can be considered immediately. Restart the local launcher to load this scheduler
change; no database migration or image rebuild is required.
Do not remove its state, imported datasets or Docker volume while work is active.

The first start freezes the model JAR and default workbooks for uploaded-data
preparation. Restarting does not silently replace that release from a changed
checkout. Release upgrades and state retirement are separate operator tasks.

Offline backups require the launcher and active writers to stop, with all model
and processor reservations settled. The backup command does not cancel jobs.
Restore uses a separate empty database/new private state and stays inactive until
verified activation. When starting an activated restored copy, supply both its
`--state` and private `--dsn-file`; this explicitly selects the restored database
without changing the usual local database startup or authentication secrets.
See [the recovery runbook](BACKUP.md) for commands and interruption recovery.

Use `create --online` for matching recovery points while submitted models continue.
The online mode retains immutable inputs and finished output, excludes changing
workspaces, and restores interrupted attempts under their original retry rules.
Shared SingleRun registries/saved exports in the same database are included;
independent SingleRun databases are not. Activation requires explicit source
isolation. Scheduling/encrypted off-machine copies are optional operator commands,
not actions taken by the laptop launcher. The offline mode above is still available.

## Acceptance

From JAS-mine-web, using a Python with the installed Playwright browser:

```bash
python tests/browser/batch_workflow.py
```

This runs the PostgreSQL/Docker regressions in a disposable database, then a
real browser against tiny synthetic jobs. It checks grouped uploads, signed
review/submission, shared seeds, baseline, page reopening, cancellation, retry
opt-out, owner isolation, pending-input dependencies, confirmed deletion and narrow layout. It does not rebuild model images or
repeat the full native SimPaths preparation proof. Reports are retained under
`~/simpaths-benchmarks/`; its temporary database and model files are removed.
If the queue suite passed and only a browser check needs repeating, add
`--browser-only` to skip rerunning that unchanged suite. Its report explicitly
records that the queue tests were skipped.

## Still to integrate

Production SMTP setup, approval administration, shared SingleRun/MultiRun
admission, hard filesystem quotas, diagnostic-log retention,
approved aggregate publication and Policy Impacts Visualiser integration remain
separate work. The results page offers permitted detailed downloads but does not
produce visualiser reports. Provider microdata is never exposed by these routes.
The previously reported model
receipt-flag reproducibility issue remains with the SimPaths maintainers.

## Editing replacement parameter workbooks

Use the download symbol beside an original file in **Workbooks you can replace**
to save that Excel file unchanged. Edit it locally, recalculate and save, then
upload it with exactly the same filename. The filename itself still opens the
browser editor. This also provides the editing workflow for workbooks containing
formulas, which remain read-only in the browser.

Only existing release defaults whose exact filenames appear in
`deploy/multirun/public_workbooks.py` can be viewed or downloaded. This declaration
is separate from preparation's input inventory: adding an Excel file to the input
directory does not publish it. Maintainers must review the entire workbook,
including hidden sheets, before adding a name or changing a public release
default. This is an access allowlist, not automatic detection of microdata.
Never put restricted microdata into a public default or point release defaults
at prepared/provider datasets. The browser cannot select arbitrary paths or use
these controls to download databases, population CSVs or UKMOD donor files.

Under **Create input dataset**, expand **Workbooks you can replace**. The list
comes from the frozen model release and uses the same filenames as preparation
validation. It excludes `DatabaseCountryYear.xlsx` and `EUROMODpolicySchedule.xlsx`,
which preparation generates. Click a filename to inspect or edit an original copy,
or click an uploaded replacement to continue editing that copy.

**Save replacement** selects the saved copy and shows **(updated)** and its latest
**Updated:** time, alongside **Uploaded:** if the edited copy came from an upload.
The original stays unchanged. Confirming deselection
uses the original workbook for the new dataset. A same-name upload with identical
contents reuses the edited copy; changed contents can replace it after confirmation.
Existing submitted preparations keep the exact
version they were reviewed and submitted with; editing does not change old jobs.
If another browser changes the file while you edit, saving fails instead of
silently overwriting that newer version. Close and reopen the latest copy.

This is a cell-value editor, with worksheet selection and paged rows/columns.
Numeric, text and boolean cells retain their types. Dates and formula cells are
read-only. Workbooks containing formulas can be viewed but must be changed and
recalculated in Excel, then uploaded, to avoid stale calculated values. XLSX saves
retain other workbook entry contents; advanced legacy XLS features may not survive
its SheetJS export. Workbook saves remain subject to upload space limits and the
existing isolated preparation checks. No model image rebuild is needed.


### Storage and problem notifications

**Storage** next to **My Jobs** lists your upload allowance, prepared datasets and
simulation outputs. Preparation working files appear only as a service-managed
space total: they include temporary copies and diagnostic records, and can be
large while preparation runs or after a failure. Shared free disk space is shown
separately. It lists only your own data. Detailed sizes use MiB; the shared-space
and allowance summary uses GiB. Recorded creation, update/use and execution dates
appear on the right. Output entries show experiment, configuration and attempt
references so repeated names and retries can be distinguished.

Use **Delete upload** for unused files, or the dataset controls for the same
dependency review available in **My Datasets**. Referenced uploads stay protected.
**Delete output files** opens a review identifying the exact finished attempt and
approximate space reclaimed. Confirming permanently removes its output once any
existing download has finished. Job history, diagnostic logs outside the output
tree, input datasets and other attempts stay intact. Results and job history show
**Output deleted**; removed output is no longer available for download or visualiser
comparisons. The Results page links to its Storage entry. Active execution files
and preparation working files cannot be deleted through this output action.
Automatic expiry of usable outputs is optional as described below. Failed-attempt
working files and diagnostic logs have the separate cleanup policy below.

Jobs unable to pass the initial disk-space check remain queued and recheck every
30 seconds without using up simulation attempts. Their status becomes “waiting
for storage”; after five minutes, the page explains that the shared capacity needs
operator attention. Jobs resume automatically when space and other resources are
available. Running jobs that exceed their own allowance still require review.

Problem emails are **disabled by default on the laptop**. The terminal confirms
this at startup. The service records deduplicated incidents locally so notification
routing can be tested without sending mail. Personal upload allowance and actionable
job problems are addressed to the owner; persistent shared-disk shortages are
addressed only to the configured administrator. Notices contain safe references,
not simulation logs or data. See the platform batch-queue guide for explicit SMTP
opt-in when deployment email is ready. Restart the launcher after the tests pass to
apply the additive database migrations; no model image rebuild is required.

## Retention dates and optional automatic expiry

Successful outputs normally expire seven days after validated completion. Your
prepared datasets expire seven days after creation or the end of their latest
actual simulation use, including failed or cancelled execution. Current uploads
expire seven days after upload, browser edit or the end of their latest preparation
use. Viewing or selecting an item does not renew it. Maintainer datasets do not
expire automatically.

Inputs needed by unfinished work and source uploads needed by retained datasets
are protected. Earlier successful outputs remain protected while another
configuration in that experiment is unfinished. Jobs awaiting review keep these
holds until resolved, cancelled or expired. Jobs requiring user intervention have
seven days to resolve the problem; My Jobs shows their deadline. With automatic
cleanup enabled, an unresolved job becomes expired and releases its input holds.
This does not immediately delete its inputs. History, other dependencies and
successful companion results remain subject to their own retention rules.
Shared-storage waits, historical pre-launch disk failures and their blocked
preparation dependants remain protected for operator recovery. Storage, My Datasets and Results show dates or
the reason deletion is postponed. When a hold ends, users have at least 96 hours
before removal. Superseded upload versions can be removed without another warning
once no dataset or unfinished work needs them.

The first start with migration 014 gives existing laptop files a fresh seven-day
window. Restarting does not reset it. The normal launch command needs no new flags:
it records dates and notices but sends no emails and performs no automatic expiry
deletion. Existing confirmed manual deletion still works. Startup and the page
state that automatic deletion is disabled. Migration 015 adds failed-job deadlines
and gives existing failures a fresh seven-day window. Automatic job expiry also
stays disabled in the normal laptop command; restart to apply the migration.

For a service with working SMTP, `--notification-emails --admin-email <address>`
enables problem and expiry delivery. `--retention-cleanup` additionally enables
automatic deletion and requires the email option. Enabling it after a preview
period gives a fresh warning window of at least 96 hours. Do not enable these flags
for the current laptop acceptance run; it captures mail and deletes only disposable
test files.

Expiry warnings are scheduled 96 hours before deletion, with a final reminder
24 hours before it. The second reminder neither extends retention nor shortens
the first warning period. Unresolved jobs receive an immediate notice and the
same reminder intervals before their recovery deadline. Viewing the page does
not extend the deadline; retry remains subject to existing attempt/time budgets.
Expiry warnings identify an item by the reference shown on the page and give its
deadline in UTC. Changed deadlines supersede earlier notices for that same item;
unsent stale notices, including final reminders, are cancelled. Both reminders
are scheduled for the revised deadline. Notices never attach data or diagnostic logs.
The 96-hour interval starts when the warning is recorded; operators must monitor
delivery failures. A mail outage does not indefinitely suspend deletion.
Experiment-completion emails are described below; automatic visualiser reports
remain future work.

Cleanup rechecks dependencies, keeps active downloads safe, and retries interrupted
removal. It preserves job history. Prepared-input export/re-import remains future work.

## Failed and cancelled working files

After migration 016, the normal launch command automatically retires unusable
working files from failed and cancelled attempts, including earlier failures. It
first confirms that the attempt has finished, that its container has been removed,
and that no result reader is using the files. Cleanup runs before further queue
admission so reclaimed disk space is available to the next job. No new launch flag
or image rebuild is needed; stop/restart the server to apply the change.

Copied input files, temporary databases, unused partial output and unregistered
failed-preparation copies are removed. Source uploads, registered prepared datasets,
successful configurations and individually validated repetition outputs are kept
under their own lifecycle. A retry still uses a new private workspace with the
original frozen inputs and seeds. Incomplete validated output remains incomplete;
it is not silently used in comparisons or offered as a successful configuration.

Private diagnostic records are retained for 30 days after attempt termination.
Log capture is bounded to 2 MiB per attempt and 128 MiB per execution pool; if that
allowance is full, logs may be omitted while compact failure/version references
are retained. Storage shows the cleanup status and diagnostic deadline. Logs are
never automatically emailed or made available through an administrator page.
An explicitly approved investigation can arrange a bounded hold before cleanup;
see the platform batch-queue guide for the internal operator hook and limits.

This internal cleanup is independent of `--retention-cleanup`: unusable scratch
does not need a 96-hour warning. Automatic expiry of usable results, datasets and
uploads, unresolved-job expiry, and real email delivery remain off with the normal
laptop command. Compact identity/removal receipts and database history remain, so
an almost-empty execution directory can persist after its large files are removed.

## Experiment-completion emails

The normal laptop command now records completion notices, with **real delivery
still disabled**. No new flag or model image rebuild is needed. Restarting the
server applies migration 017 and starts the notification checks alongside the
existing problem/expiry notifier. No emails are sent by the acceptance tests.

Once all configurations have finished or stopped for review, a notice summarises
successful, failed and cancelled configurations and links to the experiment's
Results page. Queued retries, pending inputs, active work and temporary shared
storage problems do not trigger completion mail. A failed companion does not
discard successful results. The message includes current known output deletion
dates or holds, and does not claim that a visualiser report is ready.

Links require normal sign-in and current access; they contain no login token.
Messages contain references and counts, without experiment names, input values,
microdata, raw logs or attachments. Provider-data download restrictions still
apply. The recipient is the registered, verified and currently approved owner.
Local console codes simulate verification; real inbox verification remains a
production requirement.

Restarting the server preserves pending notices and delivery records. A manual
retry suppresses stale unsent outcomes and may produce an updated outcome notice.
Delivery failures retry separately from simulations, with the same Message-ID;
an SMTP acknowledgement lost during a crash can still produce a duplicate email.
Existing unfinished work is followed on upgrade; already finished historical
experiments do not generate a backlog.

Real delivery uses the existing explicit `--notification-emails --admin-email
<address>` option and configured platform SMTP, shared with problem and expiry
messages. Leave that option off for laptop testing. A future deployed service
must use its public HTTPS origin for Results links, rather than this localhost
launcher's address. See JAS-mine-web `docs/batch-queue.md` for the delivery contract.

### Enabling delivery when the service is ready

1. Configure `SMTP_HOST` and `SMTP_FROM_EMAIL` in the service environment, plus
   `SMTP_LOGIN_EMAIL` and `SMTP_PASSWORD` if the server requires authentication.
   `SMTP_PORT` defaults to `587`; `SMTP_USE_TLS` defaults to `true` and uses
   STARTTLS. Store credentials in the deployment's private secret storage.
2. Supply the administrator recipient with `--admin-email <address>` and add
   `--notification-emails` to the existing launch command. This enables completion,
   problem and expiry emails together. Automatic deletion remains a separate
   `--retention-cleanup` option.
3. Test first in a separate service containing only controlled test addresses,
   including following a Results link through sign-in. Enabling delivery on an
   existing service can send pending notices that are still relevant; inspect its
   pending notifications before enabling delivery for all users.
4. For the VM deployment, connect verification-code delivery to email and supply
   the public HTTPS address to the browser and notification services. This is
   remaining deployment integration: the local launcher still requires console
   codes and creates loopback links, even with `--notification-emails` enabled.
5. Monitor mail delivery failures and pending notices after launch. Mail delivery
   retries independently of model execution and retains its records on restart.

No model image rebuild is needed for these mail settings. The platform guide's
**Enabling real email delivery** section documents the settings and production
integration boundary. Real email delivery remains disabled for laptop development.

## VM Visualiser development preview

The opt-in [VM Visualiser connection](VISUALISER.md) reuses the existing charts
with aggregate results processed on the server. Add `--visualiser-build PATH
--visualiser-preview` to the ordinary launch command after creating a pinned build.
Completed own-input and explicitly verified public Quick Start configurations can
be selected in Results; raw provider downloads remain unavailable. Other provider
data is excluded while its release controls and the updated calculation interface
are pending. The current preview shows levels and disables the old VM delta view.

The guide documents offline building, limits, private cache recovery, the retained
local-folder option and synthetic acceptance commands. It requires no image
rebuild, changed model settings or real email delivery.
