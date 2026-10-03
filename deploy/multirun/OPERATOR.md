<!-- (C) Copyright 2026, by Ross Richardson

Read-only SimPaths Online operator status, interpretation and disposable validation.

@author ross richardson
-->

# Operator status

Inspect an existing SimPaths Online service without starting its applications or
altering submitted work. This command provides terminal output and a versioned
JSON report. It uses the selected resource pool, verified retained releases and
the existing private state directories.

## Laptop

```bash
cd ~/git/SimPathsWeb/SimPaths
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.operator_status \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web"
```

The default state is `~/simpaths-multirun-local`, pool `simpaths-local`, schema
`jasmine_batch`. Use `--state` for another existing state directory. The command
inspects the known local PostgreSQL container and reads its retained password;
it never creates or starts containers, volumes, directories or credentials. The
database must already be running. An unavailable database produces a partial
report with filesystem/release information where it can be verified.

For an existing shared pool or a privately managed local database, specify its
actual connection explicitly. Keep connection strings in a private, service-owned
file rather than shell arguments:

```bash
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.operator_status \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --state /path/to/existing/multirun-state \
  --dsn-file /path/to/private/postgres.dsn \
  --pool existing-pool --schema jasmine_batch
```

## VM

Run as the service user, using the installed environment/source and the existing
VM TOML configuration:

```bash
python -m deploy.multirun.vm_web status --config /etc/simpaths-online/multirun.toml
python -m deploy.multirun.vm_web status --config /etc/simpaths-online/multirun.toml --json
```

The equivalent standalone form is `python -m deploy.multirun.operator_status
--config /etc/simpaths-online/multirun.toml`. This reads the configured credential
file and existing pool; it does not run the application's startup/preflight,
perform migrations, create a pool, verify SMTP or reconstruct its workers.
Use the actual installed config path if it differs from the example.

## Output

Add `--json` for the `simpaths.operator.status.v1` machine-readable report. Counts
remain complete; unfinished job details default to 20 and can be increased with
`--limit 1..200`. `details_truncated` identifies an abbreviated job list.

- **Resources:** configured CPU/RAM/storage capacity, interactive holdback,
  reservations by MultiRun/SingleRun/result processing and remaining batch
  capacity. These are scheduling reservations, not samples of live CPU or RAM
  consumption. The scheduler's own resource functions calculate availability;
  holdback and actual interactive occupancy are not counted twice. Unfinished
  or expired-lease reservations remain occupied.
- **Jobs:** simulation/preparation counts by state; bounded job/experiment
  references, attempts, last outcome and reasons such as preparing inputs,
  blocked inputs, disk shortage, retry backoff, owner concurrency or shared
  resources. `awaiting_dispatch` does not promise immediate admission: worker
  health, fresh input validation and queue order still apply. Storage wait
  quantities come from the recorded last check, whose timestamp is included.
- **SingleRun:** registry states are read only for reservations linked to the
  selected shared pool. Binding/deployment mismatches or unreadable registries
  produce unknown states while their reservations stay counted. Independently
  configured SingleRun services/pools are outside this inventory.
- **Cleanup and retention:** pending/failed reviewed output deletion, failed
  attempt cleanup, diagnostic expiry, due retention and dependency protections.
  Counts are existing records; status does not reconcile or execute cleanup.
  Due records can remain visible with automatic deletion disabled.
- **Email:** pending problem/expiry/completion notices, active delivery claims
  and notices awaiting retry. A retry count records attempted or claimed delivery,
  not proof that an email failed or reached its recipient. Resolved/cancelled or
  already-sent notices are excluded from pending counts.
- **Filesystems and caches:** ordinary file sizes and free space for state,
  uploads, artifacts, execution and releases; distinct retained prepared datasets;
  recorded download/Visualiser states, expiry and invalid receipts. Cache state
  is persisted state, not a claim that its child process is alive or that a user
  currently has permission to open a ready result.
- **Releases:** verified retained release IDs/images/resource policies and the
  selected default. Selection is distinct from the last application launch's
  release. A running application loads a changed selection on restart; accepted
  jobs keep their original pins, as explained in [RELEASES.md](RELEASES.md).

Application assembly records secret-free flags in mode-0600
`operator-settings.json`. The status command labels these **last application
launch** settings, with their timestamp. They include notification delivery,
automatic retention cleanup, Visualiser enablement and the loaded model release.
They do not claim the application is still running. A service launched before
this feature reports its email delivery setting as unknown until its next normal
restart; status does not create the missing file or guess from SMTP environment
variables. Database retention settings are reported separately.

Files are measured using the existing workspace inventory: the larger of logical
size and allocated file blocks, with an entry bound. Links/special files or
unreadable directories yield unknown sizes, not zero. Missing service areas have
zero files; missing retained prepared datasets are unknown. Categories overlap:
execution includes caches, and prepared locations can also be under artifacts.
Do not add those categories or free-space figures across shared filesystems.
Physical workspace quotas and host-wide Docker/PostgreSQL/backups remain separate
deployment concerns.

The SQL inventory uses one read-only, repeatable-read transaction with bounded
statement/lock timeouts and no scheduler row/advisory lock. Filesystem and release
observations are taken afterward and can change while the service runs. No owner
email, browser token, model parameters, filenames, raw input/output contents,
private paths or diagnostic text appear in the report. Filesystem measurement
uses metadata; receipt checks read bounded service metadata, and release
verification hashes retained scientific code/default workbooks. Exception text
is redacted because connection errors can contain credentials.

Exit code **0** means required inventories were collected; it does not mean every
job succeeded or every queue is empty. Exit code **2**, `complete: false` and
fixed warning codes identify unavailable/inconsistent sections or invalid input.
Unknown sections must not be treated as empty or used to release reservations,
delete files or retire releases. The command is a local operator tool, not a
public HTTP/admin endpoint.

## Disposable validation

```bash
cd ~/git/JAS-mine/JAS-mine-web
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --test-pattern test_operator_status.py \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/operator_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements.txt"
```

This checks 14 generic inventory cases and 35 release/CLI/application cases with
disposable PostgreSQL, fictional inputs and a synthetic executor. It sends no
emails, runs no scientific simulations, needs no image rebuild and leaves existing
services/data untouched. Checks cover read-only transactions, pool-lock independence,
combined occupancy, another pool, scoped interactive registries, preserved leases
and files, bounded/safe receipts, interrupted delivery records, frozen release
verification, legacy inventory without migration, partial failures, JSON and
private runtime flags. Local validation passed 32 cases. The disposable host
proof passed all 49 cases with no skips, and temporary resources were cleaned
up (`postgres-queue-20261003-142214`).

The reusable `database_inventory` helper returns safe report metadata and private
prepared-location references separately. `filesystem_inventory` reports sizes;
`ReleaseRegistry.inventory()` verifies retained releases without catalogue
upgrades or creating lock files. Future matching backup/restore tooling should
reuse these interfaces and validate its own complete dependency set rather than
parse terminal output or assume this bounded status report is a backup manifest.
