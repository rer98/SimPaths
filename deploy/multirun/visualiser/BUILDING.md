<!-- (C) Copyright 2026, by Ross Richardson
Reproducible dependencies, provenance and acceptance for the hosted Visualiser build.
@author ross richardson -->

# Build the hosted Visualiser integration

Updated PR sources include `FolderStructureHelp.js`. The builder includes its
source hash and compiles the shared component; the compatibility adapter leaves
the shared folder guidance and scenario-selection lifecycle intact. Older pinned
checkouts remain supported through the existing adaptations. Recheck both the
standalone App and hosted build when updating these shared interfaces.

The integration has its own `package.json` and `package-lock.json`. Its six direct
dependencies are React, React DOM, D3, Babel core, Babel's React preset and
Webpack. The tested lock contains 140 packages. Upstream's application checkout
has its own separate development tooling.

React 19.2.7, React DOM 19.2.7, D3 7.9.0 and Babel 7.29.7 remain pinned.
[Webpack 5.112.0](https://github.com/webpack/webpack/releases/tag/v5.112.0) satisfies
the resolved schema-utils peer requirement. The first isolated attempt retained
5.107.2 and produced an invalid peer tree; that attempt is preserved as failed
resolution evidence and is not the selected lock. The corrected tree passes
`npm ls --all`. No incompatible peer override is part of the configuration.

## Install and build in a fresh directory

The repository lock freezes the complete dependency graph.
[`npm ci`](https://docs.npmjs.com/cli/v11/commands/npm-ci/) installs it without
updating that graph; `--ignore-scripts` disables package lifecycle scripts.
The qualification used Node 22.23.1. Record and review the installed Node/npm
versions for a future host rather than inferring their security from an npm scan.

```bash
(
  set -e
  cd ~/git/SimPathsWeb/SimPaths
  compiler_directory="/tmp-codex/visualiser-compiler-$(date +%Y%m%d-%H%M%S)"
  mkdir "$compiler_directory"
  cp deploy/multirun/visualiser/package.json \
    deploy/multirun/visualiser/package-lock.json "$compiler_directory/"
  cp COPYRIGHT.md "$compiler_directory/"
  npm ci --ignore-scripts --no-audit --no-fund \
    --registry https://registry.npmjs.org \
    --prefix "$compiler_directory"
  npm ls --all --prefix "$compiler_directory"
  node deploy/multirun/visualiser/build.cjs \
    --source "$HOME/git/SimPathsWeb/SimPaths-Policy-Impacts-Visualiser" \
    --dependencies "$compiler_directory/node_modules" \
    --output "${compiler_directory}-build"
)
```

Keep networking enabled for initial installation. An offline reinstall can use
a complete npm cache. The selected 20 October installation is
`/tmp-codex/visualiser-compiler-candidate-20261020`, with its clean-reinstall build
at `/tmp-codex/visualiser-build-20261020-compiler-b`. A clean `npm ci` reproduced
all sixteen output files and the complete manifest from the first valid build.
The compiled dependency names and versions match the earlier selected runtime
bundle. Compiler-generated JavaScript bytes changed; the selected new bundle
passed the functional and native browser acceptance described below.

## Build provenance

### Updates with retained summaries

The application installer checks whether changing the calculator would prevent
MultiRun from using unexpired summaries. It compares the calculation mode and
the hashes of `runner.cjs` and `calculation.cjs` before activation. A
presentation-only update can proceed with the existing calculator. The guard
runs again after the private service stops to include any newly protected summaries.

If an upgrade reports `IncompatibleVisualiserUpdate`, keep the serving release
in place while deciding which results the new definitions can reproduce. When
raw output is available, validate recalculation before replacing an existing
retention promise. When it has been deleted, a calculation requiring finer
detail can only be offered for a new simulation. Any historical results must
identify their calculation version and meet the current disclosure rules.

`build.cjs` loads its tools from the supplied installed directory. Before
compiling it checks direct installed packages against their manifest and lock.
An exact manifest pin cannot be overridden by a matching but different installed
version/lock pair. Both metadata hashes must stay unchanged during compilation.

For each emitted chunk it collects package names, versions and relative package
paths, including nested concatenated modules. Each identity must match the
installed lock entry. Linked resources escaping the dependency directory fail.
`build.json` records the manifest/lock hashes, package count and separate emitted
inventories for `visualiser.js` and `calculation.cjs`. Included-module membership
is conservative because tree shaking may omit some code. Embedded vendored code
and the host Node executable require separate review.

The dependency audit verifies the recorded lock hashes and emitted versions,
and includes the emitted identities in its supplemental package inventory.
This provenance is private build metadata; the website's data flow is unchanged.

## Required acceptance

Run the metadata guard checks, annual streaming/parser and seed-paired parity
checks, compiled charts and the real ZIP-to-local-chart round trip. The optional
JSDOM checks use a separately supplied test dependency directory. The production
compiler manifest contains only the build/runtime dependencies listed above.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex node deploy/multirun/visualiser/test_build_metadata.cjs &&
SIMPATHS_VISUALISER_TEST_BUILD=/tmp-codex/visualiser-build-20261020-compiler-b \
node deploy/multirun/visualiser/test_runner.cjs
```

Run `tests/browser/batch_visualiser.py --browser-only --private-rpc --build ...`
from JAS-mine-web for the real Chromium, authentication, permission, caching,
download and local-reopen workflows. Use fictional data and a disposable
database. Collect a fresh advisory scan of this lock and the selected bundle.
Retain independent image/host acceptance and the frozen scientific benchmark
runtime. Build success or an empty advisory result does not promote a release.

## Qualification recorded on 20 October 2026

The clean locked reinstall reproduces all sixteen build outputs and the complete
manifest. Metadata guards, the actual annual parser and paired statistics,
compiled charts and the server-ZIP/local-chart round trip pass locally.
`/tmp-codex/visualiser-compiler-native-20261020/model-proof/report.json` records
sixteen passed native browser/private-service checks. Its build manifest matches
the selected output, with no browser/private-processor errors and complete
shutdown/cleanup. Native same-UID RPC framing is exercised; installed-host UID
isolation is separate.

The exact selected lock and emitted package identities were scanned in
`/tmp-codex/dependency-audit-20261020-hardened/scan-20261020-211105`. The lock
contains 140 installed package entries representing 139 inventoried identities;
no advisories matched. The upstream application's separate CRA lock still has
findings and is not the selected integration compiler. Compact evidence is
retained in `solveit_SimPathsWeb/security-review/2026-10-20/hardening-evidence.json`.
These checks did not promote a release or alter scientific benchmark runtimes.

## Opening results with a historical calculator

A retained summary records the Node version, calculation mode and hashes of the
runner and calculator that produced it. The November build can also host the
earlier calculator. Its exact runner, calculation bundle, original manifest
and notices are in `historical/f6384fa6`. Pass `--historical-source` a checkout
of the Visualiser integration at `c99f2533603e65f3f9666d5110b16628527b2975`.
That checkout matches the per-file hashes of the reader that was built for
this edition. The compiler verifies the archived manifest, reader sources and
calculator bytes. To use a copy of the original release directory, also pass
`--historical-build`. Supply `--current-build` with
the qualified current bundle to preserve its exact calculator bytes when
building the combined reader.

MultiRun chooses the calculator from the retained source records. Historical
preparation is available on request until the existing summary protection
expires. Its baseline and scenarios must use the same calculation. Both the
connected Visualiser and the downloaded package use that edition's chart
definitions. An unrecognised calculation needs a separately qualified reader
and calculator. Preserve their build artifacts while protected summaries refer
to them. An application update checks database references before retiring a
registered historical calculator.

## Checking an update from Reese’s main branch

Run the checker in the SimPaths checkout with Node v22.23.1 and the project’s
Python test environment, including JAS-mine-web and Playwright Chromium.
Specify a full commit SHA from Reese’s main branch and a new output directory.

```sh
python -m deploy.multirun.visualiser.check_candidate \
  --revision "$visualiser_commit" --output "$qualification_directory"
```

The checker fetches Reese’s repository into a temporary checkout. To use a
previously fetched copy, pass `--repository /path/to/Visualiser`. It checks the
identified upstream main ref and builds the exact requested commit. For an
offline rehearsal, `--dependencies` and `--test-dependencies` accept installed
`node_modules` directories with the pinned compiler and test package locks.
Use `--frontend` when JAS-mine-web is outside the usual checkout location.

The output contains the candidate build, a per-check log, a JSON report and
the row-by-row differences from a released experiment fixture. The fixture
uses fictional baseline and scenario data, two matching seeds, four years
and groups above and below the suppression threshold. The checker compares
MultiRun with the candidate’s own local-folder parser and the reference
calculator. It also exercises retained results after raw deletion, exports
them, and opens the downloaded charts offline with the matching reader.

`historical/approved.json` identifies each supported calculation by its
archived manifest, Node version and both calculator file hashes. Each entry
pins the exact Git commit for the browser reader. Add the reviewed original
files and notices under `historical/` when registering another edition.
A change to the required Node runtime needs separate compatibility work.

A successful run still calls for scientific review when calculation sources or
fixture values change. An unrecognised source edit or a change only to the
compiled calculator bytes calls for technical review. Inspect `report.json`,
`comparison.json` and the logs. The manifest checksum identifies the exact
bundle under review. A failed check or skipped Chromium run gives a nonzero
exit status. The scheduled workflow uses the same command.


## Scheduled checking and maintainer reports

The **Check Visualiser updates** workflow lives in the SimPaths repository.
It checks the tip of Reese’s main branch once a day at 03:17 UTC when enabled.
If several commits arrive between checks they are tested together at the newest
commit. The source SHA stays fixed throughout that run.

For a rehearsal, open **Actions → Check Visualiser updates → Run workflow** and
enter a full Visualiser commit SHA. A manual run always performs the checks.
GitHub requires the workflow file on the repository’s default branch for manual
dispatch and scheduling. The current development branch is `add-web-files`.
After the end-to-end trial, a repository administrator can enable the daily
check in **Settings → Secrets and variables → Actions → Variables** by setting
`VISUALISER_SCHEDULE_ENABLED` to `true`. [GitHub workflow triggers](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

The workflow pins the JAS-mine-web revision used for the compatibility tests.
Update that pin when adopting a new integration version. If that repository
is private, configure the Actions secret `VISUALISER_FRONTEND_READ_TOKEN`
with project-managed, read-only contents access to it. The checkout retrieves
the Python package used by the tests and discards its Git credentials.

Open the workflow run in **Actions** to see the outcome, changed-row counts,
source revisions and build-manifest checksum. A completed check saves the
exact bundle, JSON reports and logs as a run artifact for 30 days, subject
to the repository’s retention settings. A failed check saves available
diagnostics and the next scheduled run tries again. The run log also covers
failures before the checker starts.

An unchanged candidate with a retained artifact from a successful check of
the same deployment inputs on the same branch reuses that run. Its summary
links back to the original report. Changing the integration pin, deployment
code or workflow triggers a fresh check. Scientific and technical review
outcomes mean the checks completed and a decision is still needed. This
workflow records the result for a separate installation stage.

## MultiRun administrator review

Open **MultiRun administration → Visualiser Update**. Each report links to the
GitHub run and the upstream diff, identifies the build by its SHA-256 and gives
the fixture row counts. Open the comparison artifact in the run to inspect the
changed values. A failed qualification is visible for diagnosis. Routine checks
are recorded automatically. A scientific or technical exception opens a review.

Any signed-in MultiRun administrator can confirm approval or defer a review.
Deferral requires a reason visible to the other administrators. Cancel closes
the form. Two concurrent confirmations of the same choice record one decision.
For conflicting choices, the second administrator sees the first decision and
can reconsider. Reopen a deferred review or withdraw an approval while it is
still pending installation. Both actions record the reason and return the build
to **Awaiting review**. The history identifies the administrator and time for
each decision. Approval makes the tested build eligible for the server installer.

### Connecting a project repository

Install the matching JAS-mine-web and SimPaths changes together using the
schema-changing maintenance procedure. Migration 051 creates the review,
decision and notification tables. Migration 052 adds installation tracking and per-decision notifications. The application-only updater requires an
unchanged database schema. Provision the report publisher on the target
environment before enabling daily checks.

1. Generate a random 32-byte key and represent it as 64 lowercase hexadecimal
   characters. Store it privately in the executor's state directory as
   `visualiser-report-key`, owned by the executor service user with mode 0600.
   The private executor reads it at startup. Provision it again when restoring
   to a new server. Rotate both copies together.
2. In the SimPaths repository create a GitHub Actions environment named
   `visualiser-reporting`. Restrict its deployment branches to the trusted
   integration branch during rehearsal and the maintained release branch at
   handover. Put the matching key in the environment secret
   `VISUALISER_REPORT_KEY`. Set the repository variable
   `VISUALISER_REPORT_ORIGIN` to the target MultiRun HTTPS origin, without a
   trailing slash. Use the rehearsal server for the first trial.
3. Run **Check Visualiser updates** for a known commit. A separate GitHub job
   signs and posts the small qualification report after the checker finishes.
   The candidate code runs on a different runner. The publisher can register a
   report. Administrator sessions and installation credentials are separate.
4. With notification delivery enabled in MultiRun, each currently approved
   administrator on the configured administrator email list receives an email
   for a new review or a failed qualification. The link opens the review tab
   after administrator sign-in. SMTP failures retry with the same message ID.
   Each recorded approval, deferral, reopening and withdrawal also emails the
   currently authorised administrator team, including the administrator who
   acted. The message identifies the administrator, decision time, Visualiser
   revision and any reason. A cancelled form, duplicate choice or conflicting
   click creates no notice. Each recipient receives decisions for a build in
   order. Repeating a report preserves decisions and avoids new email.
5. Confirm the review appears, test cancellation and a conflicting decision
   with two administrators, and inspect the recorded history. The delivery job
   fails visibly if the server cannot accept the report. Rerunning it is safe.

### Installation rehearsal

The server-side installer stages the bundle from a qualification ZIP in a
private transaction directory. It checks the supplied GitHub archive digest,
both integration revisions, the Visualiser revision, qualification and
comparison evidence, and each asset against the reviewed manifest. The
transaction identifies both the expected installed manifest and its replacement.

Under the application update coordinator, installation uses drain mode. It
checks retained summary dependencies before and after the MultiRun services
stop. It exchanges the two complete build directories in one Linux filesystem
operation and checks the bytes served for the Visualiser page, scripts,
styles and images over HTTPS. The old directory remains available for
rollback. An interrupted installer can recover using the durable host receipt
and the two manifest digests.

Run the local regression tests from the SimPaths repository root:

```sh
python3 -m unittest deploy.multirun.test_visualiser_bundle_installation deploy.multirun.test_update_package
```

### Connecting the deployment worker on a rehearsal server

The host worker uses a project-owned, read-only GitHub credential when the
SimPaths repository is private. The report-signing key stays separate. Grant
the project credential Actions read access to that repository. The worker
checks the workflow path, event, trusted branch, project revision, successful
qualification job and artifact from the run attempt. The signed storage
download receives no GitHub credential. [GitHub workflow run permissions](https://docs.github.com/en/rest/actions/workflow-runs)
and [GitHub artifact downloads](https://docs.github.com/en/rest/actions/artifacts).

1. Install the matching coordinator, admin code and database migrations using the
   schema-changing maintenance procedure. Install the three units from
   `deploy/multirun/vm/simpaths-visualiser-{update.service,update.timer,mail.service}`
   in `/etc/systemd/system`. Create `/opt/simpaths-online/visualiser-updates`
   on the Visualiser filesystem, owned by root with mode 0700.
2. Set the trusted project and branch in root-owned
   `/etc/simpaths-online/visualiser-updater.toml`. For the current fork use
   the following values. At handover use the upstream repository and its
   maintained deployment branch.

   ```toml
   repository = "rer98/SimPaths"
   branch = "add-web-files"
   ```

3. If authentication is required, provision a root-readable project credential
   and add `LoadCredential=github-token:/etc/credstore/simpaths-github-token`
   to the update service's `[Service]` drop-in. The updater reads the systemd
   credential. Keep SMTP settings in the existing MultiRun SMTP configuration.
4. Reload systemd and enable the timer on the rehearsal host. It checks the
   local queue about once a minute. The rehearsal deployment should serve MultiRun
   at its configured HTTPS origin and SingleRun health on port 8443 of that host.
   The independent mail unit uses MultiRun's
   existing notification settings and can deliver a failure while the executor
   is stopped, provided PostgreSQL and SMTP are available.
5. Manually qualify one known upstream revision. Check the decision email and
   the **Visualiser Update** tab. Withdraw an approval during drain and check
   that admissions resume. Approve it again, verify the served charts and
   retained results, then test a verification failure and verified rollback on
   the rehearsal host before enabling the timer in production.

A routine build or approved exception must still follow the installed upstream
revision. If the calculator changes, its qualification must cover the installed
calculation and an administrator must have approved it. A differing scientific
reference requires registering the installed calculator and selecting it as the
qualification reference before testing the next scientific change. After a failed download or expired
artifact, run the qualification workflow again for the same commit. A matching
build can reuse its prior review decision. The worker records an installation
failure in the tab and queues mail for each authorised administrator.

If automatic rollback fails, admissions stay paused. The failure notice and
the review tab contain the update reference. After correcting the reported
host problem, the operator can retry rollback for that exact reference using
the installed Python and the coordinator's usual trusted import path:

```sh
sudo /opt/simpaths-online/venv/bin/python -I -B -c \
  "import sys;sys.path.insert(0,'/opt/simpaths-online/SimPaths');from deploy.multirun.visualiser.install_worker import main;main()" \
  --retry-rollback UPDATE_ID
```

Recovery rechecks the previous build and service health before resuming
admissions. Keep the root-staged transaction directory for any outstanding
installation or recovery. Completed transaction directories retain the other
build for operator inspection.
