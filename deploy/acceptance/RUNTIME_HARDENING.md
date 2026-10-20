<!-- (C) Copyright 2026, by Ross Richardson
Guarded exclusion of an unused inherited executable with preserved image lineage.
@author ross richardson -->

# Model runtime hardening

The 20 October exact-image scan found thirteen Go advisories in the inherited
`/usr/bin/pebble` executable. A read-only inspection of the qualified training
image confirmed that it is not owned by a Debian package and that Temurin's
certificate entrypoint does not call it. The application launches Xvfb/Java.
Background: [the Adoptium discussion](https://github.com/adoptium/containers/issues/1064).

The inspected identities are:

| File | SHA-256 |
| --- | --- |
| `/usr/bin/pebble` | `5e33a1304f207bb275b1b4cc3dc4c05fdd88755f5c705e2fd569924ec72dc061` |
| `/__cacert_entrypoint.sh` | `4f41c720f4050a7389a1b6a3b69c322d417d8134338cc07e1be86cf82d650a63` |

All three model recipes carry the same exclusion block. It checks the entrypoint
hash, refuses a changed executable, a symlink, a package-owned file or an
unexpected package-query error, then removes the audited executable. It also
accepts an already excluded executable when reusing a prepared base. Changing
the pinned runtime or these hashes requires a fresh inspection. The retained
Quick Start packager and compact-image path propagate the same block.

## Apply the exclusion to qualified images

`runtime_hardening.py` can derive candidates directly from a passed isolated
qualification. It builds one small filesystem layer per image. Each result must
retain every parent layer and all runtime configuration (including its user,
entrypoint, command and environment), and have the identical application JAR and
certificate entrypoint. The executable must be absent. Each result runs
`java -version` through its original entrypoint as the original non-root user,
with no network or host mounts and bounded CPU/RAM/processes.

This reuses the base's completed browser acceptance under verified layer and
configuration preservation. The new checks are native startup checks; they do
not rerun the seven browser workflows or scientific simulations. The receipt
states this scope. Original images and benchmark artifacts remain available.
Exclusion changes the final runnable filesystem; bytes in the original parent
layers remain in Docker's content storage.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/acceptance/runtime_hardening.py \
  --qualification /tmp-codex/isolated-security-refresh-20261020-191021/report.json \
  --output /tmp-codex/model-runtime-hardening-20261020
```

Use a fresh output directory for another run; `--dry-run` prints the exact parent
image IDs without Docker access. The real run needs 3 GiB free and acquires the
same quiet-campaign lock as the image and research workflows. Only its private
build references and inspection/startup containers are cleaned up. Candidate
images and logs are retained on failure.

The 20 October native run passed for all four roles; its receipt is
`/tmp-codex/model-runtime-hardening-20261020/report.json`. It records unchanged
protected benchmark artifacts and no promotion. The audit preparer accepts this
receipt only with its matching base qualification, driver, recipe/startup hashes,
four distinct new image identities and complete lineage/startup evidence.

The fresh exact-ID scan completed at 21:15 UTC on 20 October:
`/tmp-codex/dependency-audit-20261020-hardened/scan-20261020-211105/report.json`.
Each derived image has 252 inventoried packages, no high/critical findings and
55 Ubuntu entries (47 medium, 8 low) with no fixed distribution version recorded.
The thirteen former Pebble entries are absent; the nine previously verified
distribution-package floors remain unchanged. The remaining OS findings still
require applicability and deployment decisions. Compact evidence is retained in
`solveit_SimPathsWeb/security-review/2026-10-20/hardening-evidence.json`.

## Verification and release

Create a fresh audit plan with the hardening receipt and the selected Visualiser
build/lock. Scan the actual derived IDs. The earlier scan remains evidence for
the parent images and must not be relabelled as a scan of these candidates.
Review remaining distribution findings and complete the new MultiRun and VM
acceptance separately before release promotion.

Local guard tests run the real recipe block against temporary fictional files;
they check successful exclusion, safe reapplication and refusal of changed,
owned, linked or unreadable components. Provenance tests reject altered launch
settings, unrelated layers and incomplete base acceptance.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python -m unittest \
  deploy.acceptance.test_runtime_hardening -v
```
