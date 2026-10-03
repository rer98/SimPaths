<!-- (C) Copyright 2026, by Ross Richardson

MultiRun model release registration, compatible upgrades and immutable resource policies.
@author ross richardson
-->

# Retained MultiRun model releases

An operator can register a newer, reviewed SimPaths release and select it for new
input preparation. Existing datasets, accepted reviews, queued configurations and
retries continue using their original JAR, runtime image and resource allowances.
Selecting a new default does not upgrade previously prepared datasets. Prepare a
new dataset to use the new model. This is separate from choosing a SingleRun image
in the interactive catalogue; SingleRun sessions already retain their chosen image.

## What is retained

`STATE/releases/catalogue.json` records a default ID and fingerprints of all
registered manifests. Each `release-<sha256>` directory contains:

| File | Purpose |
| --- | --- |
| `release.json` | Immutable name, image ID, JAR/default/adapter hashes, supported web contract and resource policy |
| `model.jar` | Exact MultiRun model binary |
| `defaults/` | Parameter workbooks; generated country/year and policy-schedule workbooks are excluded |
| `PrepareDataset.java` | Preparation helper compiled against this release's JAR |
| `run.sh` | Container script retained for datasets prepared under this release |
| `COPYRIGHT.md` | Generated metadata attribution; copied code/data retain existing notices and licences |

The release ID covers the complete identity, including default workbooks and
resource policy, rather than only the JAR. Registration copies and hashes ordinary
files; it rejects linked paths, publishes a complete bundle before the catalogue,
and serialises competing updates. Startup verifies every retained bundle and
rejects missing, altered or unsupported releases. Interrupted catalogue publication
can adopt an identical complete bundle on repeat registration. Incomplete staging
directories are not selected or used.

Legacy `STATE/release/` installations are registered under their original
`local-<jar-hash-prefix>` ID, retaining their original directory and manifest bytes.
Old reviews/preparations are not rewritten to the new format. They retain the
existing helper-hash check. Keep the compatible hosting code needed by these
legacy preparations; registration cannot reconstruct a lost historical helper.

New prepared-input receipts include the release ID, supported contract, resource
policy and run-script hash. The script is copied into the retained dataset beside
its JAR. Dispatch checks the receipt and uses this script, not a newly edited
checkout copy. Legacy datasets and standalone proof/import tools retain their
existing allocation and runner behavior. Native simulation settings and seeds are
still frozen separately in each queue specification and `options.txt`.

## Register and select a version

Run as the service account from the reviewed SimPaths checkout, using its Python
environment. Commands manage release files only: they do not launch simulations,
send mail, pull images, alter database jobs, or retire older versions.

First build and test the MultiRun JAR/defaults and install the approved Java runtime
image. The runtime image is not the source of `model.jar`: MultiRun mounts its
retained JAR and inputs into that image. Record source commits, build/acceptance
evidence and immutable image IDs alongside the deployment records.

```bash
cd ~/git/SimPathsWeb/SimPaths
release_image_id="$(docker image inspect simpaths-interactive:uk-user-data --format '{{.Id}}')"
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.releases register \
  --state "$HOME/simpaths-multirun-local" \
  --image "$release_image_id" \
  --name 'SimPaths UK — reviewed October release' \
  --jar "$HOME/git/SimPathsWeb/SimPaths/multirun.jar" \
  --defaults "$HOME/git/SimPathsWeb/SimPaths/input" \
  --make-default
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.releases list \
  --state "$HOME/simpaths-multirun-local"
```

Omit `--make-default` to stage a version without selecting it. The command prints
its content-based ID. Registering the identical bundle again is safe and keeps its
recorded name. A changed JAR, image, workbook, helper, runner or resource policy
produces a separate ID. A name change alone cannot replace an existing record.

To select a staged version or return to a retained one:

```bash
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.releases select \
  --state "$HOME/simpaths-multirun-local" --release RELEASE_ID_FROM_LIST
```

Restart the application to load the selected default and retained list. A running
application keeps its already loaded selection until restart; registration does
not change it halfway through a request. Local startup uses the registry's pinned
image rather than following a moved image tag. First startup with no registry
snapshots the reviewed checkout automatically, preserving the existing setup flow.

For the native VM use `/srv/simpaths-online/private/multirun` as `--state` and the
installed VM environment/source paths. Set `[model].image` in `multirun.toml` to the
selected release's immutable image ID before restarting. Startup rejects a
registry/configuration mismatch; `preflight` verifies all retained runtime images.
The first VM start can bootstrap from its reviewed installed checkout as before.

**Compatibility:** coexisting versions must implement the supported web profile,
preparation API and output contract. Registration freezes bytes and declared
contracts; it is not scientific or executable compatibility certification. Test a
new model with the existing preparation/run/result acceptance before selecting it.
An incompatible scientific API/schema/output update needs a reviewed hosting
adapter change, preserving support for retained jobs, before rollout. Existing
JAR-version checks still prevent mixing different model JARs within an experiment.
Training images with different populations may share the same model JAR.

## Resource policy interface

The immutable release manifest owns this policy; new prepared receipts carry it
forward. Accepted configurations record calculated CPU, RAM and storage allocations
in PostgreSQL. Waiting configurations resolve from the policy in their preparation
definition/receipt, and retries use the accepted job allocation. Selecting a new
default or restarting never recalculates these from the newest policy. Pending
datasets have a provisional input-size estimate, described below.

Newly registered releases start with the agreed **4 GiB fixed working allowance
plus 512 MiB per repetition**:

```json
{
  "format": "simpaths.multirun.resources.v1",
  "preparation": {"cpu_millis": 2000, "memory_mib": 5120, "storage_mib": 12288},
  "simulation": {
    "cpu_millis": 2000,
    "memory_mib": 4096,
    "large_memory_mib": 5120,
    "storage": {"setup_mib": 4096, "per_repetition_mib": 512}
  }
}
```

Memory above 20,000 simulated people uses `large_memory_mib`. For a ready dataset:

```text
fixed MiB = max(configured setup MiB, ceil(input-copy minimum bytes / MiB))
working MiB = fixed MiB + planned repetitions × per-repetition MiB
```

The input-copy minimum counts one private input copy, one native first-run copy
of top-level workbooks/databases, the model JAR and a 1 GiB reserve. Larger inputs
raise only the fixed term, rounded up to a whole MiB. They are not multiplied by
the repetition count. The allowance covers a complete configuration attempt:
its sequential repetitions share the working directory.

For inputs fitting the 4 GiB fixed term:

| Repetitions | Working allowance per attempt |
| ---: | ---: |
| 1 | 4.5 GiB |
| 3 | 5.5 GiB |
| 12 | 10 GiB |
| 1,000 | 504 GiB |

Submission review shows the calculated allowance, with a compact per-configuration
list when datasets require different amounts. A configuration exceeding the pool's
configured capacity after interactive holdback is rejected before submission.
A busy pool does not reject work that fits its capacity: it waits in the queue.
Increasing the repetition cap alone does not increase the pool's available storage.
The 1,000-repetition parser ceiling is not an allocation for a laptop or staging VM.

**Pending datasets:** the review initially uses the configured fixed term because
the prepared database size is not yet known. The review labels this provisional.
Once preparation completes, the service computes the input-copy minimum using the
same frozen release policy. If that raises the allocation beyond the pool's capacity,
the configuration is blocked without spending an attempt or execution time; review
replacement inputs or the operator's capacity plan. The final allocation and input
identity are stored before admission and retained by retries.

**Existing releases and jobs:** already registered policies, legacy bundles and
prepared receipts are not rewritten. Their original fixed 10 GiB policy remains
10 GiB where recorded. Restarting an existing service does not opt it into the new
default. To use scaling, register/select a new release using the command above;
the changed policy gives it a distinct ID even if the scientific JAR is identical.
No image rebuild is required for this policy-only change. Prepare new user-input
datasets with that release. Legacy training/input receipts without a policy use
the explicitly selected configuration's retained release policy for new reviews;
already accepted configurations retain their previous release selection. Standalone
proof commands without an explicit policy keep their historical allocations.

The `list` command and startup log show the default policy's fixed/per-repetition
terms. The default is an initial operator estimate to measure on representative
workloads, not a guarantee for every population, horizon or collector selection.
Runtime continues using the separately frozen launcher formula.

An operator policy can be supplied as a private, service-owned JSON file using
`register --resource-policy /path/to/policy.json`. It must be mode 0600 (or stricter),
bounded and without links. Policies cannot lower supported execution minima or
exceed integer/allocation bounds. A policy change creates a new release identity;
existing datasets must not have their policy edited. The shared pool must have
sufficient CPU, RAM and storage for the admitted profiles. Browser requests and
scientific YAML cannot provide these allocations.

### Working copies, cleanup and physical space

Large native `output/<timestamp>/input` snapshots are made for the first repetition
only. Every repetition has its own `input/options.txt`. After a finished attempt's
container is confirmed removed, the service reclaims its private input copy, temporary
files and the large native snapshot, keeping each `options.txt`, verified CSVs and
logs. This cleanup does not delete the reusable prepared dataset or job history.
Retained datasets/results, uploads, diagnostics, aggregate caches and prepared ZIPs
need separate disk budgets; they are not covered by a running attempt reservation.

For scaled policies, admission and the last check before copying require the full
calculated working allowance to be free on the execution filesystem. An input-size
minimum remains an additional floor. Old fixed policies retain their previous
physical launch checks. Low space waits without consuming an execution attempt.
The executor monitors actual workspace growth and stops an oversized attempt.
These are shared reservations and size checks, **not hard filesystem quotas**.
Provision the dedicated private filesystem and reserve room for all retained data,
images, PostgreSQL, logs and caches as described in [VM.md](VM.md).

### Full-length storage measurement

```bash
cd ~/git/SimPathsWeb/SimPaths
~/simpaths-browser-tests/venv/bin/python deploy/multirun/storage_proof.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --prepared /tmp/codex-rer/multirun-prepared-50000-20260925 \
  --output "$HOME/simpaths-benchmarks/multirun-storage-$(date +%Y%m%d-%H%M%S)"
```

This uses disposable PostgreSQL and the verified public 50,000-person training
dataset. It runs 2019–2026 with one and three repetitions, one model at a time,
using the new policy. Stop local models first; allow approximately 40 minutes,
6 GiB available RAM and 7.5 GiB free on the temporary-work filesystem. No scientific
JAR/image rebuild or real email delivery is needed. Source inputs are verified
unchanged, and only this proof's stopped containers/workspaces are removed.

The report records once-per-second allocated-byte peaks for the workspace, input
copy, native snapshot and CSVs, plus per-seed output sizes and remaining headroom.
Required annual CSVs, seed mappings and settings are checked by the normal adapter.
It verifies that only the first repetition made a large snapshot and that production
cleanup preserves verified CSVs and every `options.txt`. Evidence contains counts,
hashes and private diagnostics, not copied input datasets or raw CSVs. Sampling can
miss brief peaks; the normal running-workspace monitor stays enabled throughout.
Measure other supported populations, longer horizons/collectors and the intended
production VM before treating these initial settings as calibrated production limits.

## Retention, image protection and backups

There is no automatic release or image retirement in this feature. Retain all
registered bundles and image IDs, including older prepared-training source images.
An old version becoming non-default does not make it unused. Do not delete
`release/`, individual release directories, prepared JARs or run scripts to force
an upgrade. Do not run broad image pruning as an upgrade step. Existing catalogue
promotion/retirement tooling needs explicit `--keep-image` protection for MultiRun
runtime and prepared-image IDs; its catalogue/rollback checks alone cannot discover
every queued MultiRun dependency.

Backups must include `releases/`, any legacy `release/`, private prepared datasets
and their scripts, plus the matching PostgreSQL state and existing credentials.
Keep private paths stable on restore. `ReleaseRegistry.load()` returns the verified
default-first inventory for future operator-status and backup tooling; those tools
should reuse this inventory rather than parse human-readable `list` output. Future
release retirement must inspect queued/running/retry/preparation/dataset references
before reclaiming bundles or images. It is deliberately outside default selection.

## Disposable transition proof

```bash
cd ~/git/JAS-mine/JAS-mine-web
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --proof-only \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/release_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements.txt"
```

The proof checks registry integrity/recovery, legacy identity, frozen helper/run
script, resource policies and real HTTP/PostgreSQL transitions using fictional
inputs. An old signed review submits after default selection; queued preparation
publishes under the old release; waiting simulations and retries retain that model,
seeds and allocations after reconstruction. New inputs use the new version, mixed
JAR comparisons and another owner's inputs are denied. It launches no scientific
models and sends no real emails. Separate model acceptance is required for each
actual newly built scientific release.

Storage-scaling checks on 3 October: 175 runnable MultiRun cases and the two
JavaScript selection/storage-review checks pass locally. The 23 local skips
require disposable PostgreSQL/container environments. All 99 expanded transition
checks passed without skips in `postgres-queue-20261003-124101/model-proof/report.json`,
including calculated storage, old signed allocations, pending-size increases,
retries and rejection of excessive repetition counts. All 12 generic storage
checks passed in `multirun-storage-20261003-124138/tests.log`, including per-configuration
capacity rejection and signed allocation preservation. The full-length model
measurement in that latter directory is running; no production calibration claim
is made from the bookkeeping checks.

Initial release-support validation on 3 October 2026: all 85 transition-proof cases passed without skips
in `postgres-queue-20261003-112048/report.json`. The local MultiRun suite also
passed 167 runnable cases; 17 PostgreSQL/container cases require the disposable
database or container environment. The JavaScript dataset/model-selection check
passed separately. These bookkeeping checks do not replace scientific acceptance
of a newly built model.
