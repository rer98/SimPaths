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
