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

This first connection uses the available Visualiser revision
`9a904b52c8a7d2306d3a9e5d175cf2614de0c0ee`. Its existing calculations provide
Baseline and Scenario **levels**. The VM page disables its old delta view;
seed-paired policy impacts and their uncertainty await the updated calculation
interface. This is a development preview for owner-supplied inputs and explicitly
verified public Quick Start datasets. Other provider data remains unavailable.
The current source's suppression threshold is not treated as approval to publish
restricted data. Scientific definitions and release rules remain with the
Visualiser/SimPaths teams.

## Build the maintained application

The builder reads a trusted Visualiser checkout, preserves upstream notices and
records its commit, source hashes, dependency versions, build toolchain and asset
hashes. It copies neither the default aggregate dataset nor raw example data.
Use a clean checkout of the pinned revision, separate from any branch containing
unfinished upstream contributions. The local branding build uses
`/tmp/codex-rer/visualiser-source-9a904b5-20260930` for this purpose.
The original checkout is not changed. `adapt_app.cjs` applies guarded data-source
edits to a generated copy of `src/App.js`, replacing its bundled-default loader
and Connect Data controls with the authenticated VM/local adapter. It retains
the upstream page design and variable/domain definitions rather than copying them
into a separate maintained page. Changed upstream integration anchors stop the
build for review. The scientific calculation modules are unchanged by this page
adaptation. The preferred future arrangement is an upstream-supported aggregate
data-source interface; that contribution has not yet been agreed with Reese.

Charts, dependencies, PMH/UKRI logos and the upstream interpretation/citation pages
are served locally. Guidance styles are extracted into local CSS, Google Fonts
requests and inline image handlers are removed, and resource links use the VM
asset route. Installed fonts provide the normal fallback. There is no CDN,
default-data request or source map, and the default CSV is not deployed.

For this laptop, the installed Debian Node/Babel/webpack toolchain and verified
cached dependencies can build without downloading packages:

```bash
cd ~/git/SimPathsWeb/SimPaths
/usr/bin/node deploy/multirun/visualiser/build.cjs \
  --source "$HOME/git/SimPathsWeb/SimPaths-Policy-Impacts-Visualiser" \
  --dependencies "$HOME/.npm/_npx/668c188756b835f3/node_modules" \
  --output "$HOME/simpaths-multirun-local/visualiser-build-20260929"
```

Choose a new output directory for each build. React and React DOM are `19.2.7`,
D3 is `7.9.0`; the tested system tools are webpack `5.76.1` and Babel `7.20.12`.
On another VM supply a reviewed dependency directory containing those packages
and Babel/webpack. Review licensing/contribution arrangements before distributing
an upstream-derived public build; this checkout declares no standalone licence.
Generated builds include integration attribution and dependency licence files.
They are deployment artifacts, not source files to commit.

## Enable the local preview

Add both flags to the existing launcher:

```bash
  --visualiser-build "$HOME/simpaths-multirun-local/visualiser-build-20260929" \
  --visualiser-preview
```

For the maintained-application temporary build with the current SimPaths Online
branding, use `--visualiser-build /tmp/codex-rer/visualiser-build-20260930-d` instead. Keep that
directory while the service uses it; build a persistent copy for longer use.
No model image rebuild or repeat simulation is required. Restart applies platform
migration 019 and starts the private processor alongside the existing worker.
The normal command without these flags keeps the feature disabled.

In Results, choose **Baseline for visualisation** and **Scenario for visualisation**,
then **Prepare Visualiser**. With no baseline, select one configuration in the
Scenario field. When processing finishes, **Open Visualiser** opens the charts.
Pair selections assign roles without moving or renaming native output, and retain
the user's configuration names, dataset references, model revision and seed map.
Two selected configurations must belong to the same experiment, have complete
verified output, match seed plans and remain accessible to their owner. A different
prepared dataset in each configuration is supported.

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
10 per owner; published JSON is bounded to 16 MiB and 50,000 aggregate rows.
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
its own 16 MiB serialization/checksum bound. The 256 KiB experiment-settings limit
remains unchanged. Existing verified ready reports retain their links across the
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

**Visualise Locally Saved Data** uses the existing folder scanner and calculation
workers in the browser. Raw local files stay local and are not uploaded. Returning
to **View Online Results** clears that source and reloads only authenticated aggregates.
Combining local and VM sources in one comparison is deliberately deferred because
statistical compatibility needs a separate design.

The generated integration makes only practical compatibility changes: safe
tooltip text, a CSP-compatible CSV object converter using D3's row parser,
termination/counting fixes for reused local workers, and fixed display mappings
for native employment enum spellings and the pinned parser's UK region aliases.
For example, `North East (England)` is displayed as `North East`, matching the
chart vocabulary. These mappings change labels after aggregation; they do not
regroup records or change numerical scientific calculations.
The VM application and guidance pages allow their existing inline styles but do
not enable `unsafe-eval` or inline scripts. Locally generated data/Blob images are
allowed for the upstream PNG export; remote resource loading stays prohibited.
Unknown categories fail publication rather than leaking arbitrary raw strings.

## Acceptance

```bash
cd ~/git/JAS-mine/JAS-mine-web
~/simpaths-browser-tests/venv/bin/python tests/browser/batch_visualiser.py \
  --build /tmp/codex-rer/visualiser-build-20260930-d
```

The default runs the complete backend suite, then a real-browser proof with
fictional native CSVs and the actual pinned chart bundle. It exercises shared
capacity, refresh, source roles/names, aggregate-only network traffic, permission
changes, source deletion and six local runs processed on two workers. Add
`--browser-only` after a separately passing backend suite, or
`--backend-pattern test_visualiser.py` for a focused integration rerun.
It uses disposable PostgreSQL, creates no production data and sends no email.

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
SIMPATHS_VISUALISER_TEST_BUILD=/tmp/codex-rer/visualiser-build-20260930-d \
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
SIMPATHS_VISUALISER_SOURCE="$HOME/git/SimPathsWeb/SimPaths-Policy-Impacts-Visualiser" \
SIMPATHS_VISUALISER_DEPENDENCIES="$HOME/.npm/_npx/668c188756b835f3/node_modules" \
  /usr/bin/node deploy/multirun/visualiser/test_app.cjs
```

## Production web-server boundary

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

When Reese publishes the updated source, review its aggregate/pair interface,
pin a new build, consume server-computed seed-paired impacts, and compare reference
outputs. Agree which fields and exports are permitted for restricted provider
results before enabling them. Keep statistical methods in the Visualiser source;
authentication, queue capacity, input permissions and retention stay in the service.
