<!-- (C) Copyright 2026, by Ross Richardson
Private VM aggregation, pinned chart builds and the development connection.
@author ross richardson -->

# VM to Policy Impact Visualiser connection

The opt-in development connection opens the maintained Visualiser application
from an experiment's Results page. Its original header, topic sidebar, variable
descriptions, explanatory panels, logos and credits come from Reese's source.
The VM reads its verified native Person and BenefitUnit
CSVs and publishes aggregate rows through an authenticated API. The browser does
not fetch VM CSVs, databases, raw records, private per-run summaries or logs.

The service is branded **SimPaths Online**, with **UK MultiRun** beneath its
webpage title. Visualiser and guidance-page navigation uses **Return to SimPaths
Online**. **SimPathsWeb** remains the development project name, and **JAS-mine-web**
is the underlying web software; those technical names are not the service title.
The Visualiser labels service-generated output **Online results**, alongside
**Visualise Locally Saved Data** for output selected from the user's computer.

The updated development connection uses Reese's 7 October multi-scenario charts
and seed-paired calculations, plus her 9 October README update at
`fdd05894c535b4d442d458a546c4b6e6e97f7247`. Ross's two proposed PR branches merge
that revision; the tested connected source revision is
`b981df532220144f8e1a7936c557eef5c05deb5a`. The baseline and all selected
alternatives are shown together, with individual scenario toggles and named exports.

This remains a development preview for owner-supplied inputs and explicitly
verified public Quick Start datasets. Other provider data remains unavailable.
The implementation's level suppression is `total_sample < 20`, pooled across
runs; the newest upstream README instead describes `min_sample < 20`. Paired
impact fields are calculated independently of suppressed levels. The integration
preserves those upstream calculations: this threshold is not approval to publish
restricted data. Scientific definitions and release rules remain with the
Visualiser/SimPaths teams, including any rules for paired impacts and exports.

## Build the maintained application

The builder reads a trusted Visualiser checkout, preserves upstream notices and
records its commit, source hashes, dependency versions, build toolchain and asset
hashes. It copies neither the default aggregate dataset nor raw example data.
Use a clean checkout of the reviewed connected branch. The persistent isolated
checkout for this update is `/tmp-codex/visualiser-update-20261009/repository`.
It preserves the original PR histories and includes the newest upstream main;
the user's original checkout is unchanged. `adapt_app.cjs` checks the reusable
`dataSource` interface and maps the locally served interpretation page. It no
longer replaces the upstream loader or maintains a second page implementation.
The connected interface, browser security corrections and export fixes remain
reviewable changes in Ross's PR branches; no scientific formula is rewritten.

Charts, dependencies, PMH/SimPaths/UKRI logos and the upstream interpretation/citation pages
are served locally. Guidance styles are extracted into local CSS, Google Fonts
requests and inline image handlers are removed, and resource links use the VM
asset route. Installed fonts provide the normal fallback. There is no CDN,
default-data request or source map, and the default CSV is not deployed.

For this laptop, the reviewed checkout has its locked dependencies installed.
The application builder reads that directory without package downloads:

```bash
cd ~/git/SimPathsWeb/SimPaths
node deploy/multirun/visualiser/build.cjs \
  --source /tmp-codex/visualiser-update-20261009/repository \
  --dependencies /tmp-codex/visualiser-update-20261009/repository/node_modules \
  --output /tmp-codex/visualiser-build-YYYYMMDD-paired
```

Choose a new output directory for each build. React and React DOM are `19.2.7`,
D3 is `7.9.0`. The build receipt records the actual Node, webpack and Babel
versions; reproduce that receipt when preparing a deployment build.
On another VM supply a reviewed dependency directory containing those packages
and Babel/webpack. Review licensing/contribution arrangements before distributing
an upstream-derived public build; this checkout declares no standalone licence.
Generated builds include integration attribution and dependency licence files.
They are deployment artifacts, not source files to commit.

### Refresh Ross's two PR branches

The isolated branches are descendants of the original PR branches and latest
upstream main. The verified bundle is retained at
`/tmp-codex/visualiser-update-20261009/visualiser-pr-updates-v3.bundle`.
The focused browser proof now passes. Import the verified commits and push Ross's fork:

```bash
cd ~/git/SimPathsWeb/SimPaths-Policy-Impacts-Visualiser &&
git fetch /tmp-codex/visualiser-update-20261009/visualiser-pr-updates-v3.bundle \
  refs/heads/fix/local-processing-security:refs/remotes/local-update/fix/local-processing-security \
  refs/heads/feat/aggregate-data-source:refs/remotes/local-update/feat/aggregate-data-source &&
git switch fix/local-processing-security &&
git merge --ff-only refs/remotes/local-update/fix/local-processing-security &&
git switch feat/aggregate-data-source &&
git merge --ff-only refs/remotes/local-update/feat/aggregate-data-source &&
git push origin fix/local-processing-security feat/aggregate-data-source
```

The fast-forward guards stop if the original branches have acquired other changes.
No force push or write to Reese's repository is needed. Existing PRs update when
their source branches are pushed; Reese retains control of merging them.

## Enable the local preview

Add both flags to the existing launcher:

```bash
  --visualiser-build /tmp-codex/visualiser-build-20261009-paired-c \
  --visualiser-preview
```

Keep that build directory while the service uses it; copy or rebuild it in the
chosen deployment location for longer use. No model image rebuild or repeat
simulation is required. Restart the service and prepare the selected results again
to generate aggregates with the new build's identity. Older verified aggregate
links retain their original data; legacy level-only results keep their delta view
disabled. The normal command without the preview flags keeps the feature disabled.

In Results, choose **Baseline for visualisation** and **Scenario for visualisation**,
then **Prepare Visualiser**. With no baseline, select one configuration in the
Scenario field. When processing finishes, **Open Visualiser** opens the charts.
Pair selections assign roles without moving or renaming native output, and retain
the user's configuration names, dataset references, model revision and seed map.
Two selected configurations must belong to the same experiment, have complete
verified output, match seed plans and remain accessible to their owner. A different
prepared dataset in each configuration is supported.
Older verified builds without the v2 reader retain single/pair controls; upgrading
the hosting code does not offer sets until the application build can display them.

For several alternatives, tick **Compare several alternatives**, choose the
baseline and tick the required configurations. **Select all available alternatives**
excludes the baseline and unavailable configurations. Up to 99 alternatives can
be selected, within the experiment's configured limit. **Prepare Visualiser**
processes each baseline/alternative repetition once. Private per-run summaries
feed one joint accumulator, with each alternative retaining its own identity;
actual seed values determine matching, rather than processing order. Progress counts all
selected repetitions. Selection and progress survive refresh after submission.

The generated link opens the maintained application with the full aggregate set
loaded. All selected alternatives appear alongside the baseline. Scenario buttons
show or hide individual alternatives without merging their values. Display names
are plain text, separate from stable configuration IDs. Refresh reloads the full
set; returning from local files does the same. Chart CSVs include the displayed
alternatives and configuration names, retaining individual identities in delta
exports. PNG legends include every displayed alternative and wrap long names.

The aggregate API keeps single/pair results in `simpaths.visualiser.v1`. A set
uses `simpaths.visualiser.v2`: `comparison` names the baseline and ordered alternative
configuration IDs; `data.series` contains one `{configuration, rows}` entry for each
configuration. Rows use the fixed aggregate schema, with four optional paired fields:
`paired_mean_delta`, `paired_lower_ci`, `paired_upper_ci`, `paired_n_runs`.
Legacy rows without those fields remain supported. Mean/share, wage-bin,
income-bin and population-pyramid metrics are admitted explicitly; unknown
fields and nonfinite JSON values remain rejected. Baseline rows retain
`scenario: "baseline"`; each separate alternative retains `scenario: "scenario"`.
Consumers must use the enclosing configuration ID to distinguish alternatives.
No private run summaries or raw records are added. Each API read rechecks every
selected source's permissions, including deletion/revocation of the last alternative.
Requests specify job UUIDs; published series identify frozen configuration IDs.

The Results page also offers **Include several alternatives** for comparison ZIPs.
See [completed-result downloads](LOCAL_WEB.md#viewing-and-downloading-completed-results)
for the numbered folders, input deduplication and versioned manifest. Existing
two-configuration downloads remain compatible with the local folder picker.

Queued progress and ready links survive a browser refresh. A failed preparation
can be requested again; this repeats aggregation, not the simulations. Provider
aggregate permission does not confer permission to download provider raw output.
Completion emails continue linking to Results; they do not claim charts are ready.
Real email delivery and optional automatic retention deletion stay disabled with
the ordinary laptop command.
Retry replaces the previous progress/error entry for the same Baseline/Scenario
selection. Separate comparisons retain their own status and are labelled with
configuration names. Refresh removes obsolete or unavailable saved cache entries.

## Processing and retention

One processor runs per execution root. Its admission reserves 1 CPU, 1,024 MiB
memory and 512 MiB storage from the same PostgreSQL capacity pool as simulations.
Model admission counts this reservation too. Busy capacity or disk space leaves
the request queued. Default processing time is 600 seconds; operators can set
`--visualiser-memory-mib` (512–4096) and `--visualiser-timeout-seconds` (60–3600).
Node's heap limit is 256 MiB below the reservation. This limits heap allocation;
it is not an OS-level memory or CPU sandbox. Production process isolation and
large-output resource measurements remain deployment work.

Private intermediate metrics are processed one run at a time, never sent to HTTP,
and removed on success/failure. The cache is bounded to 256 MiB, 64 selections and
10 per owner. Single/pair publications are bounded to 16 MiB and 50,000 aggregate
rows. Comparison sets are bounded to 64 MiB including metadata, 50,000 rows per
configuration and 200,000 rows in total; the same fixed aggregate fields and
finite-value checks apply. A set may exceed these bounds before the selection
limit; processing fails without changing simulations, and smaller selections can
be prepared separately.
These are development bounds, not a claim that the current parser streams a large
individual CSV: it still reads that run's two files into memory. Benchmark typical
long simulations and the updated parser before selecting production limits.

The private `execution/visualiser-cache` holds durable queue/status receipts and
verified aggregate artifacts for up to 24 hours. Source deletion, revoked approval
or lost dataset access immediately denies further API reads. Retirement removes
the inaccessible/expired cache; it never extends the source results' retention.
No login token is stored there. The Node calculation bundle and runner are not
public assets. Diagnostics are private, bounded and discarded; HTTP reports
controlled errors rather than exception contents.
The complete aggregate artifact, including its public provenance metadata, has
its own serialization/checksum bound (16 MiB for a single/pair, 64 MiB for a set).
The separate 2 MiB experiment-settings limit remains unchanged. Existing verified ready reports retain their links across the
encoding correction. Operator diagnostics identify the failed processing stage
without printing raw values or exception contents.

Processor and output-reader locks are inherited by child processes. A restarted
processor releases a stale capacity reservation only after obtaining the lock,
which proves any surviving old child has stopped. Interrupted work restarts from
verified native output; existing simulations are not rerun. A graceful service
shutdown terminates processing children, leaves the request queued and waits for
their termination before releasing resources. Output deletion waits for active
readers, and a deletion request prevents a newly finished aggregate being published.

## Local files and browser security

**Visualise Locally Saved Data** uses the existing folder scanner and serial
streaming parser in the browser. Raw local files stay local and are not uploaded. Returning
to **View Online Results** clears that source and reloads only authenticated aggregates.
Combining local and VM sources in one comparison is deliberately deferred because
statistical compatibility needs a separate design.

The generated integration makes only practical compatibility changes: safe
tooltip text, a CSP-compatible CSV object converter using D3's row parser,
the connected aggregate interface, complete multi-scenario exports, robust
variable-name matching, and fixed display mappings
for native employment enum spellings and the pinned parser's UK region aliases.
For example, `North East (England)` is displayed as `North East`, matching the
chart vocabulary. These mappings change labels after aggregation; they do not
regroup records or change numerical scientific calculations.
The VM application and guidance pages allow their existing inline styles but do
not enable `unsafe-eval` or inline scripts. Locally generated data/Blob images are
allowed for the upstream PNG export; remote resource loading stays prohibited.
Unknown categories fail publication rather than leaking arbitrary raw strings.

## Acceptance

The current focused proof is:

```bash
cd ~/git/JAS-mine/JAS-mine-web
~/simpaths-browser-tests/venv/bin/python tests/browser/batch_visualiser.py \
  --backend-pattern test_comparisons.py test_visualiser.py test_aggregate_io.py test_results.py test_input_results.py test_downloads.py \
  --build /tmp-codex/visualiser-build-20261009-paired-c \
  --output "$HOME/simpaths-benchmarks/vm-visualiser-paired-$(date +%Y%m%d-%H%M%S)"
```

It uses disposable PostgreSQL, fictional native CSVs and the real chart bundle;
it sends no email and reruns no scientific model. Checks include simultaneous
alternative charts/toggles, seed-paired deltas, complete named CSV/PNG exports,
refresh, comparison ZIPs, aggregate-only network delivery, local streaming,
owner isolation and source-deletion denial. Without `--backend-pattern` it runs
the complete backend suite; use `--browser-only` only after passing database checks.

Local checks on 9 October pass: 69 Visualiser/Jest cases, eight Node application, compiled-bundle
and comparison-mapping cases, 11 native-CSV calculation/backend cases, and 15
publication/private-helper cases. The build records its pinned source and asset
hashes. All 91 focused database checks passed in the
[backend test log](/home/rer/simpaths-benchmarks/vm-visualiser-paired-20261009-183331/tests.log).
The subsequent browser-only run, `vm-visualiser-paired-20261009-184750`, passed all
nine stages using the pinned `paired-c` build. Its
[browser report](/home/rer/simpaths-benchmarks/vm-visualiser-paired-20261009-184750/model-proof/report.json)
records no uncaught browser errors; the
[wrapper report](/home/rer/simpaths-benchmarks/vm-visualiser-paired-20261009-184750/report.json)
confirms success and temporary-database cleanup. The controlled ValueError is the
deliberate failed-preparation/retry fixture.

The initial browser attempt stopped at an ambiguous access-denied assertion;
both affected assertions now select the Online connection message. The
compiled-bundle check also verifies that a denied reload clears charts, displays
the denial and makes no default-data request. No application rebuild was needed
for that assertion correction. These tests use fictional native CSVs; they do not
measure the larger 100,000-person/2070 research workload or approve restricted
provider releases. Earlier browser reports below cover previous bundles.

Multiple-alternative acceptance passed on 3 October 2026 in
`vm-visualiser-20261003-220301` using build `multi-c`: all 77 focused backend cases
and nine browser stages passed. This includes alternative switching/names/refresh,
numbered comparison ZIP folders with frozen settings and per-run options, strict
aggregate-only browser responses, local worker reuse without uploads, owner
isolation and denial after source deletion. The report records no uncaught browser
errors and confirms temporary-database cleanup. The controlled ValueError is the
deliberate failed-preparation/retry test. Eight native-CSV/backend checks also pass
locally, including independent three-series statistics and older-build compatibility.

Acceptance passed on 29 September 2026. All 292 backend cases passed across the
full run and focused retry/Visualiser reruns. Evidence is in
`vm-visualiser-20260929-223919` (full run),
`postgres-queue-20260929-225134` (16 retry cases), and
`vm-visualiser-20260929-225207` (15 Visualiser backend cases).
The final browser proof, `vm-visualiser-20260929-230813`, passed all five stages
with build `h`: actual charts after refresh, aggregate-only network delivery,
local parsing without uploads, owner isolation and source-deletion protection.
It reported no uncaught browser errors and confirmed disposable-resource cleanup.

The model-side numerical/schema tests run separately:

```bash
cd ~/git/SimPathsWeb/SimPaths
SIMPATHS_VISUALISER_TEST_BUILD=/tmp-codex/visualiser-build-20261009-paired-c \
  ~/simpaths-browser-tests/venv/bin/python \
  -m unittest deploy.multirun.test_visualiser_backend -v
```

### UK region compatibility correction

A real-output check exposed an inconsistency in the pinned upstream source:
`parseCore.REGION_MAP` uses labels such as `North East (England)` and `Yorkshire
and The Humber`, while the chart definitions use `North East` and `Yorkshire and
the Humber`. The original publication guard correctly rejected these mismatches
after all runs had been summarised. The first synthetic fixture contained only
London, whose label already matched.

Build `i` adds an explicit mapping of those seven known aliases. All five
model-side tests pass, including every UK region, unchanged expected statistics
and rejection of unknown region text. A private read-only reproduction using the
four retained real runs verified their successful-attempt fingerprints, completed
publication and confirmed that every field except the two display-label fields
matches the untouched upstream calculation. Private intermediate files were
removed afterwards. The browser acceptance above was run with build `h`;
the browser source and statistical calculation source are unchanged by this fix.

To apply the correction, stop the launcher, change its `--visualiser-build` path
to build `i`, restart and click **Prepare Visualiser** again in Results. Existing
simulations and their outputs are reused. A new build identity creates a separate
aggregate cache entry; do not overwrite a build directory in use by the service.

### Aggregate publication and stale error correction

The follow-up service check found that final report hashing had incorrectly used
the 256 KiB experiment-specification serializer, even though aggregate reports
are allowed up to 16 MiB. The platform now applies the aggregate bound when writing
and reading the full published report. A private read-only reproduction of the
four retained runs checked source fingerprints and unchanged statistics, then
exercised the actual service publication, ready-state recording and cached read.
This report exceeds the request bound and fits within the aggregate bound.

The browser now retains only the latest request per comparison, discards legacy
duplicate errors and removes unavailable cache references after refresh. Three
publication checks, seven browser-state checks and the existing upload/polling
JavaScript checks pass locally. The focused 18-case database suite and all six
real-browser proof stages passed in `vm-visualiser-20260929-234929` using build `i`.
There were no browser errors and disposable-resource cleanup completed. The proof
covers duplicate-error removal and retry, real charts, aggregate-only delivery,
local files, owner isolation and source-deletion protection. Restart the launcher
with the existing build `i`; no additional chart build or model simulation is needed.

### Maintained application interface — 30 September 2026

Build `a` replaces the initial simplified page with the actual upstream `App.js`
application. Only data sourcing and its explanatory text are adapted; the original
layout, topic/domain navigation, descriptions, guidance and credit/feedback panels
are retained. There is no Outcome dropdown added by the hosting service. The
Connect Data panel offers **View Online Results** and **Visualise Locally Saved Data**,
and identifies the chosen configuration names. Loading/error states render no
charts until aggregate or local data is available.

The six native-CSV/backend checks, two application-rendering checks and existing
browser-state JavaScript checks pass locally. PostgreSQL/Chromium acceptance for
build `a` passed all 18 backend cases and seven browser stages in
`vm-visualiser-20260930-002053`. The proof checks the upstream interface,
logos/guidance and PNG export alongside the existing aggregate-only, local-file,
permission and source-deletion protections. No uncaught browser errors occurred
and disposable-resource cleanup completed. The generated screenshots were also
checked. The deliberate failure/retry stage prints a controlled ValueError;
it is an expected test condition. Earlier build `i` evidence remains separate.

Apply this interface by restarting the launcher with
`--visualiser-build /tmp/codex-rer/visualiser-build-20260930-a` and its existing
`--visualiser-preview` flag. Click **Prepare Visualiser** in Results to create the
new build's aggregate cache entry. Existing model output is reused; no simulation
rerun or image rebuild is required.

The application adapter checks can also run without PostgreSQL or Chromium:

```bash
cd ~/git/SimPathsWeb/SimPaths
SIMPATHS_VISUALISER_SOURCE=/tmp-codex/visualiser-update-20261009/repository \
SIMPATHS_VISUALISER_DEPENDENCIES=/tmp-codex/visualiser-update-20261009/repository/node_modules \
  /usr/bin/node deploy/multirun/visualiser/test_app.cjs
```

## Production web-server boundary

The native deployment templates and external canary/permission probe are described
in [VM.md](VM.md). Passing local browser tests does not replace checking the actual
deployed HTTPS proxy and storage mappings.

The [local HTTPS rehearsal](VM.md#local-https-rehearsal-before-choosing-a-host)
checks the deployment proxy template with fictional pair/multiple-alternative
aggregates and a compressed, interrupted/resumed ZIP after an application restart.
It uses the real access/publication routes with a synthetic calculation backend;
it complements the maintained-Visualiser browser proof and the external-host
acceptance below. It does not require choosing a VM or public domain.

**Required deployment check, recorded 30 September 2026:** the production web
server/reverse proxy must not expose private storage through static directory
mappings. Keep uploads, prepared inputs, execution workspaces/raw output,
download/Visualiser caches, logs, diagnostics and backups outside public static
roots. Disable directory listing. Publish only approved application assets and
route protected reads through the application's authorization checks; proxy
caching must not create a public copy of protected responses or archives.

Before opening the service, test the external production URL with fictional data.
Verify direct/guessed file paths and traversal attempts do not expose private
files, provider-derived raw downloads remain denied including partial/resumed
requests, and VM Visualiser network responses contain aggregates only. Record the
production configuration and test evidence. The passing laptop application tests
do not establish the safety of a future production web-server configuration.

Authorized downloads of a user's own-data results remain intentional. Selecting
locally saved files in the Visualiser is also permitted. Neither feature grants
raw access to provider-derived output or makes private VM storage a public asset.
This check is also recorded in the planning repository's TODO.txt and
PLAN_OF_ACTION.md so it is included when organising deployment.

## Next integration checkpoint

Complete the updated browser proof and submit the refreshed PR branches for
Reese's review. Before enabling restricted provider results, agree release rules
for both level estimates and paired impacts/exports, including the pooled-versus-
per-run suppression discrepancy above.

Measure server aggregation and browser rendering for the research target agreed
with Matteo: 100,000 initial people, 2019–2070, up to ten repetitions per
configuration and four concurrent users. This is a heavier workload than the
50,000-person, eight-year calibration; it requires separate resource measurements.
Server aggregation keeps raw output outside the browser but does not make large
CSV processing or large aggregate sets free of memory/time limits.
Local-folder manifest names and sweep metadata remain a separate upstream enhancement.
