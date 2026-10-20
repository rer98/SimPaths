<!-- (C) Copyright 2026, by Ross Richardson
Preserving scientific benchmark references while qualifying replacement images.
@author ross richardson -->

# Isolated model image security refresh

Use `run_isolated_security_refresh.py` to qualify security candidates without
overwriting the laptop's benchmark JARs, inputs, model catalogue or Python
environment. The existing coordinated refresh compiles in its supplied model
and core checkouts; this wrapper supplies private committed-source clones and
a new Python environment instead.

## Quick Start base preparation

The pre-refresh Docker inventory collected on 20 October contained the original
research image and the older training image, but no Quick Start images. The old
Quick Start preparation directories contained receipts without their databases.
The completed refresh used `--prepare-quickstart`. It compiles private Java sources,
prepares and independently verifies 20,000- and 50,000-person public-training
populations, and builds new base images under unique tags. It does not run
research simulations or substitute the 100,000-person research package.

The new bases are checked against the prepared profile, JAR and input hashes,
allowing only the existing, verified query-account database migration. Completed
preparation/load-check copies are removed before packaging, with their compact
properties and logs retained. A verified, persistent context replaces the bulky
preparation package before Docker starts. Failed contexts remain reusable.
The base references change only in the private frontend clone's catalogue.

The generated multi-stage recipe publishes one final input-data layer. It keeps
the same pinned runtime, distribution security floors, private reader setup,
non-root user, read-only application files, entrypoint and launch settings.
All application and input file bytes are checked when compacting a retained
image. Only a superseded intermediate tag created by this workflow is retired;
benchmark images and other projects' images are preserved. Hashes of the
generated recipe and context files are retained in each population's checkpoint.

Without this option, both catalogue base images must already exist locally;
a missing base stops the workflow before source copying or compilation.

## Run

Keep networking enabled for the fresh Python environment and distribution
packages. Docker, JDK 25, Maven, Chromium and the existing isolated Maven cache
must be available. Run outside quiet performance campaigns. The wrapper and
inner acceptance share the same nonblocking benchmark lock under `TMPDIR`.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python \
  deploy/acceptance/run_isolated_security_refresh.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --core "$HOME/git/SimPathsWeb/JAS-mine-core" \
  --maven-repository /tmp-codex/java-security-rebuild-20261014/maven-repository \
  --prepare-quickstart --prune-build-cache \
  --output "/tmp-codex/isolated-security-refresh-$(date +%Y%m%d-%H%M%S)"
```

`--prune-build-cache` explicitly permits removal of unused **shared Docker build
cache**. Quick Start cleanup uses `docker builder prune --all --force` in a
`finally` block; the earlier dangling-only prune left substantial cache behind.
It removes no unrelated images, containers or volumes. Omit it if that cache
must be retained. The workflow creates disposable inspection/browser containers
and removes the containers it owns.

The initial fresh-copy guard is 14 GiB; it is not a sufficient estimate for the
whole campaign. Population preparation requires 12 GiB free, image stages/export
have a context-size-based guard of at least 8 GiB, and browser checks still need
8 GiB. A one-second free-space watchdog interrupts and joins owned children if
available space falls below 3 GiB. It is a sampled safeguard, not a filesystem
quota or protection against arbitrary concurrent disk allocations. Atomic JSON
replacement preserves earlier checkpoints; a preallocated 4 MiB reserve permits
one retry when a checkpoint encounters a full filesystem. Recheck space before
the later quiet Gini campaign, which requires 19 GiB free.

Tracked runtime code must be committed before selection. The two pre-existing
modified input workbooks are explicitly excluded: the private clones use their
committed versions. The wrapper records excluded paths and verifies that the
original workbook bytes, both original JARs and original catalogue remain
unchanged. Untracked files are not included in the builds. Source clones share
Git objects read-only with the originals; retain the original repositories while
using these local candidates.

`--dry-run` prints the selected commits and plan without Docker access, copying,
package installation or compilation. It does not establish base availability,
free space or acceptance. `--output` must be directly inside `TMPDIR` so both
levels share the quiet-campaign lock.

## Evidence and follow-up

### Completed qualification — 20 October 2026

The recovered run completed at 20:17 UTC:

- `/tmp-codex/isolated-security-refresh-20261020-191021/report.json` records
  `passed: true`, `promoted: false` and `originals_unchanged: true`.
- Both compact Quick Start images passed their real private-RPC browser checks.
  Both interactive images built, and all four training modes plus the
  user-data upload/preparation/build/reset workflow passed.
- The child acceptance receipt matches the checksum saved by the wrapper.
  Private-service shutdown and owned-resource cleanup completed without recorded
  RPC errors. Installed-host identity isolation remains a separate check.
- Original benchmark JARs, protected workbooks and the original catalogue still
  match their saved hashes. Preservation evidence starts at recovery, as
  described below; it does not reconstruct the first attempt's lost report.

The fresh exact-artifact audit plan is
`/tmp-codex/dependency-audit-20261020-qualified/plan.json`. Its scan completed at
20:29 UTC on 20 October, with the current advisory databases. All four image IDs
match acceptance and meet the nine requested distribution-package floors.
This parent scan identified residual Pebble and native findings; the review and compact
evidence are in `solveit_SimPathsWeb/SECURITY_DEPENDENCY_TRIAGE_2026-10-15.md` and
`solveit_SimPathsWeb/security-review/2026-10-20/dependency-evidence.json`.
Qualification and scan completion did not promote any image.

The subsequent [guarded runtime exclusion](RUNTIME_HARDENING.md) derives four
new candidates with preserved layers, launch configuration and JARs. All four
pass native entrypoint/Java startup; the final exact-ID scan has no high/critical
findings and retains 55 no-fix Ubuntu entries per image. The smaller
[Visualiser integration compiler](../multirun/visualiser/BUILDING.md) also passes
its clean-rebuild, parser/chart/portable-download and native browser checks, and
its 139 inventoried package identities have no matching advisories. Final
compact evidence is `solveit_SimPathsWeb/security-review/2026-10-20/hardening-evidence.json`.
These follow-up candidates remain unpromoted.

### Receipt layout

The selected output contains private clones, the candidate Python environment,
its resolved package inventory, preparation logs and the wrapper `report.json`.
The existing model/browser acceptance output is its sibling with `-acceptance`
appended. Check both receipts. The outer receipt binds the inner report checksum
and records preservation of the original scientific artifacts.

With fresh bases, the wrapper first compiles/tests Java, verifies the images,
and runs the real detailed-access, allocation, storage and private-RPC browser
checks on both populations. It writes the existing release receipt format and
passes those checks to the coordinated driver's guarded `--resume-from` path.
That driver rechecks source/image/JAR identity before reuse, then builds
training/user-data candidates and exercises all five interactive browser paths.
This avoids another database repackaging pass and Java rebuild. The catalogue
is not promoted. If existing catalogue bases are selected instead, the original
coordinated compilation/packaging path still applies.

### Recover the 20 October disk-full preparation

The first attempt exhausted the root filesystem and truncated its outer report.
Its 20,000-person base export completed. A recovery receipt records removal of
5.67 GiB of completed preparation/load-check copies; the original zero-byte
report remains as `report.enospc.json`. Recovery verifies the actual image,
private source commits, built JARs, prepared inputs and existing Python package
inventory rather than treating the lost report as successful evidence.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python \
  deploy/acceptance/run_isolated_security_refresh.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --core "$HOME/git/SimPathsWeb/JAS-mine-core" \
  --maven-repository /tmp-codex/java-security-rebuild-20261014/maven-repository \
  --prepare-quickstart --resume-preparation --prune-build-cache \
  --output /tmp-codex/isolated-security-refresh-20261020-191021
```

This reuses the original private clones/environment and Java builds, verifies and
compacts the completed base, prepares 50,000 people and proceeds to real browser
acceptance. The preservation receipt explicitly starts at recovery, because the
first attempt's outer checksum record was lost. Both original scientific JARs,
original input workbooks and the original catalogue remain protected.

`--resume-preparation` requires the existing private output and unchanged source
commits. It accepts verified contexts/images and completed browser receipts. It
does not yet resume an already-started coordinated `-acceptance` sibling. For
that later case, use the existing guarded resume tooling from the private model
clone and bind the verified child report into a new outer receipt; do not rerun
against the original checkout or delete successful images blindly.

After qualification, create a **new** dependency-audit plan with the wrapper
receipt, selected Visualiser bundle and its actual dependency-lock directory.
The updated audit tool infers cloned model/core/frontend paths and resolved
Python packages from that receipt, requires the selected JAR to match acceptance,
freezes actual bundle assets, and scans the exact four candidate image IDs.
It no longer silently selects the historical October 14 images.

A clean scan is not automatic release approval. Review residual findings,
qualify a new MultiRun release and native input/queue path, complete the current
installed-host acceptance, and verify the selected Visualiser compiler receipt
before deployment. The 20 October compiler/emitted-package review is complete.

## Local checks

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PYTHONPATH="$HOME/git/JAS-mine/JAS-mine-web" \
~/simpaths-browser-tests/venv/bin/python -m unittest \
  deploy.acceptance.test_isolated_security_refresh \
  deploy.acceptance.test_single_rpc_rehearsal
```

These use small fictional artifacts, local Git clones and mocked Docker
operations. They do not establish real image-build or model/browser acceptance.
