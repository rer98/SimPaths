<!-- (C) Copyright 2026, by Ross Richardson

Live scheduler storage-budget controls, filesystem growth and recovery checks.
@author ross richardson
-->

# Live scheduler storage budget

The shared pool's `storage_mib` is the total storage that the scheduler may
reserve for active simulations, input preparation, SingleRun and result
processing. It is separate from retained-file accounting and each job's enforced
workspace quota. Existing actual-free-space checks still apply before admission.

Operators can increase this pool budget while the service and model jobs run.
The optional **follow** mode checks the private filesystem every 30 seconds and
adds capacity after that filesystem grows. It keeps a fixed amount of non-job
headroom and an optional absolute budget ceiling. Merely extending the provider's
disk or logical volume is insufficient: its mounted filesystem must have grown.
This controller performs no partition, logical-volume or filesystem operations.

## Enable for an installed service

Use the trusted executor account, installed Python environment and reviewed
configuration. These commands belong to the private operator interface, not the
public website. For example, from `/opt/simpaths-online/SimPaths`, as
`simpaths-online`:

```bash
/opt/simpaths-online/venv/bin/python -m deploy.multirun.storage_budget \
  --config /etc/simpaths-online/multirun.toml status

/opt/simpaths-online/venv/bin/python -m deploy.multirun.storage_budget \
  --config /etc/simpaths-online/multirun.toml follow
```

On first enablement, omitted `--headroom-mib` preserves the difference between
the existing pool budget and measured service-accessible filesystem capacity.
**Enable before growing the filesystem** to preserve that existing gap. If it
has already grown, supply a reviewed headroom explicitly; the controller cannot
infer which part of the current gap is intentionally reserved.

For example, a hypothetical 200 GiB service-accessible filesystem with a 180 GiB
pool budget has 20 GiB headroom. Following it through growth to 300 GiB raises the
budget to 280 GiB. The following explicit settings express that same headroom
and a 280 GiB ceiling; these are examples, not the initial VM allocation:

```bash
/opt/simpaths-online/venv/bin/python -m deploy.multirun.storage_budget \
  --config /etc/simpaths-online/multirun.toml follow \
  --headroom-mib 20480 --ceiling-mib 286720
```

The applied target is the smaller of:

- service-accessible filesystem capacity minus configured headroom;
- the optional ceiling, when nonzero;
- the database's integer limit.

Service-accessible capacity excludes filesystem blocks reserved from the service
account. It uses total capacity, rather than current free bytes: file creation
and deletion do not change this budget or subtract active reservations twice.
The actual-free-space and inode checks separately account for retained results,
uploads, caches and other current disk use. Review headroom for these uses; a
larger pool allowance does not make retained data disappear.

Headroom must be at least 1 GiB. It is independent of the existing interactive
and processing holdbacks, which remain inside the pool budget and unchanged.
Unallocated LVM extents and space on separate backup, database or Docker
filesystems do not count as private-filesystem capacity.

## Fixed increase or freeze

Use `set` for a reviewed fixed allowance, or `freeze` to retain the currently
applied allowance and stop following future filesystem growth:

```bash
/opt/simpaths-online/venv/bin/python -m deploy.multirun.storage_budget \
  --config /etc/simpaths-online/multirun.toml set \
  --storage-mib 286720 --headroom-mib 20480

/opt/simpaths-online/venv/bin/python -m deploy.multirun.storage_budget \
  --config /etc/simpaths-online/multirun.toml freeze
```

`set` validates that the requested budget fits the currently measured filesystem
after headroom. The first command above requires the example expanded filesystem.
Calling `follow` again retains the existing headroom unless supplied explicitly;
an omitted ceiling resets it to zero (no configured ceiling). These commands
never lower the current budget. Lowering a ceiling below the applied budget is
also rejected. Reductions require a separately reviewed, drained migration; no
live reduction command is provided.

No control file means the original strict `[pool] storage_mib` startup behavior.
Once enabled, private persistent controls supply the storage override at future
starts; the original TOML remains the fallback, not a competing storage value.
CPU, memory, holdbacks, execution concurrency and policy checks retain their
existing startup enforcement. There is no schema migration or model-image build.

## Safety and inspection

Changes take the same database pool-row lock as job and processing admission.
They change only total storage capacity. Existing attempts, reservations,
scientific settings, seeds, model versions, per-job growth ceilings and retention
policies are not rewritten. Newly available space can admit waiting work on the
next dispatch cycle, subject to CPU, memory, permissions and actual free space.

The controller accepts a dedicated mounted private filesystem on installed hosts.
An unexpected device/root replacement, invalid control, unavailable measurement
or budget that no longer fits pauses new batch admissions. The dispatcher still
observes and renews running attempts and handles their completion. It does not
cancel jobs to correct a capacity discrepancy. Backups briefly defer refresh
while retaining the previously applied budget. Restore gates exclude operator
writes until recovery has been verified and activated.

`status` prints the persisted policy, latest measured capacity and actual database
allowance as private JSON. The normal [operator status](OPERATOR.md) also reports
mode, headroom, ceiling and paused admission. `storage-budget-status.json` keeps
the latest check and at most 16 applied growth records; it is not a permanent
audit archive. Error records contain exception classes, not credentials or
private exception messages. Status inspection does not change the allowance.

Controls are bound to their pool and schema, and use private, bounded,
service-owned files written atomically. Durable
intent precedes the database commit: after interruption or a lost commit reply,
the next valid refresh/start can finish applying it idempotently. Inspect status
after a failed operator command before retrying. Online backup capture fences
control changes while selecting its database/files; online and offline backups
include the control and status. A restored service establishes the identity of
its new mounted private filesystem and validates capacity again. A smaller
recovery target cannot silently lower the restored budget.

## Disposable native proof

Local unit checks cover capacity/headroom calculations, ceilings, private files,
backup contention, corrupt status recovery and durable interrupted intent. Run
the database/runtime/recovery proof in the native terminal:

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --proof-only \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/storage_budget_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements-backup-proof.txt" \
  --output "/tmp-codex/storage-budget-native-$(date +%Y%m%d-%H%M%S)"
```

It uses disposable PostgreSQL, tiny fictional helper processes and files. It
checks competing admissions, stable running attempts/processes, unchanged shared
reservations, restart enforcement and native online/offline backup restoration.
It sends no real emails, runs no scientific simulations and changes no host disk
layout. Installed-host filesystem growth and mounted-quota acceptance remain
separate deployment checks.

### Native qualification, 22 October 2026

The first proof completed 125 checks: 124 passed, one failed, no errors or skips,
and disposable database cleanup succeeded. Its running-process growth test,
shared-reservation checks and native online/offline recovery cases passed.
Evidence: `/tmp-codex/storage-budget-native-20261022-130351/report.json` and
`model-proof/report.json`; preserve the failed report.

The failing restart fixture tried to change `per_user_active` to 3, which was
already its synthetic policy default. Accepting that unchanged policy was correct.
The assertion now uses the original value plus one and also checks a changed
runtime limit, with ordinary startup and the admission-limit update opt-in.
Production enforcement did not change. The focused rerun is:

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --test-pattern test_storage_budget.py \
  --output "/tmp-codex/storage-budget-native-retry-$(date +%Y%m%d-%H%M%S)"
```

The focused rerun passed all seven storage-budget database cases in 4.993
seconds, with successful disposable-database cleanup. Evidence:
`/tmp-codex/storage-budget-native-retry-20261022-132618/report.json` and
`tests.log`. Together with the 124 passing cases from the initial proof, this
qualifies the complete 125-case scope after the fixture correction. The initial
report remains failed and preserved; no single all-passing 125-case rerun is
claimed. All 53 local controller/configuration/operator checks also passed.
Actual mounted-host filesystem growth remains an installation acceptance check.
