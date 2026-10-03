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
default or restarting never recalculates these from the newest policy.

The initial policy preserves the current allowances:

```json
{
  "format": "simpaths.multirun.resources.v1",
  "preparation": {"cpu_millis": 2000, "memory_mib": 5120, "storage_mib": 12288},
  "simulation": {
    "cpu_millis": 2000,
    "memory_mib": 4096,
    "large_memory_mib": 5120,
    "storage": {"setup_mib": 10240, "per_repetition_mib": 0}
  }
}
```

Memory above 20,000 simulated people uses `large_memory_mib`. The defined storage
formula is `setup_mib + planned_repetitions × per_repetition_mib`; its initial
per-repetition term is zero. Calibrating nonzero storage scaling is the next
development step, using representative simulation measurements. This change does
not raise existing allowances or choose production storage estimates. Runtime
continues using the separately frozen launcher formula.

An operator policy can be supplied as a private, service-owned JSON file using
`register --resource-policy /path/to/policy.json`. It must be mode 0600 (or stricter),
bounded and without links. Policies cannot lower supported execution minima or
exceed integer/allocation bounds. A policy change creates a new release identity;
existing datasets must not have their policy edited. The shared pool must have
sufficient CPU, RAM and storage for the admitted profiles. Browser requests and
scientific YAML cannot provide these allocations.

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

Validation on 3 October 2026: all 85 transition-proof cases passed without skips
in `postgres-queue-20261003-112048/report.json`. The local MultiRun suite also
passed 167 runnable cases; 17 PostgreSQL/container cases require the disposable
database or container environment. The JavaScript dataset/model-selection check
passed separately. These bookkeeping checks do not replace scientific acceptance
of a newly built model.
