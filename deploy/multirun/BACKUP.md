<!-- (C) Copyright 2026, by Ross Richardson

Offline MultiRun backup, isolated restoration, activation and recovery validation.

@author ross richardson
-->

# MultiRun backup and restore

`python -m deploy.multirun.backup` saves a matching PostgreSQL snapshot and private
files. Restore uses a **new state directory and a separate empty database**. It
verifies both and leaves the copy inactive until an explicit activation command.
None of these commands starts simulations, sends email or runs expiry cleanup.

This first implementation requires a maintenance window. It covers one MultiRun
queue schema containing one resource pool. It does not back up standalone
SingleRun session databases or interactive model containers/volumes. If SingleRun
shares the pool, its interactive reservations must also be released first.

## What is saved

The backup contains:

- A native custom-format PostgreSQL dump of the complete queue schema, including
  owners, approvals/sessions, configurations, seeds, job/attempt history, frozen
  limits, permissions, retention dates and notification records.
- The private MultiRun state: uploads, prepared datasets, retained output,
  per-run `options.txt`, bounded diagnostic files, the existing session secret,
  release registry and retained JARs, workbooks and preparation/run scripts.
- Every distinct registered prepared-input directory outside the state directory,
  once, including imported training snapshots.
- A private manifest recording source locations, required immutable Docker image
  IDs, PostgreSQL major version, file checksums and hashes/counts of all table rows.

Regenerable download ZIP and Visualiser caches are omitted. Their SQL history is
preserved; normal application recovery can rebuild eligible cache entries from
retained outputs without rerunning simulations. Maintenance locks and an inactive
restore marker are not source payload.

**Keep deployment artifacts separately:** the installed hosting checkouts/venv,
VM TOML, PostgreSQL connection files, SMTP configuration, Visualiser build, proxy
and systemd configuration, and every required Docker image. The backup records
image IDs but does not export, pull or rebuild images. Use the normal trusted image
distribution or a separately protected `docker save` archive. Activation requires
all recorded images to be installed, including older training-source images.

The database dump is compressed. Private data files are copied without compression;
allow room for a complete second copy and the dump on the destination filesystem.
File inventories are bounded to 100,000 entries per tree and 64 MiB of metadata.
Exceeding a bound or running out of space fails without publishing a backup.

## Prepare a maintenance window

1. Prevent new submissions at the operational entry point and let current work
   finish. Include preparations, models, uploads, downloads, Visualiser processors
   and deletion/recovery work. Do not cancel users' jobs just to make a backup.
2. Use [operator status](OPERATOR.md) to inspect reservations and unfinished work.
   Queued jobs and jobs awaiting review may remain recorded.
3. Stop the MultiRun launcher/service gracefully. **Stopping the web process does
   not stop an already launched model container.** If one is still running or its
   reservation is unreleased, allow normal service recovery to settle it before
   stopping again. A shared SingleRun session must use Leave and release its
   allocation; closing its page or launcher is insufficient.
4. Inspect the service's labelled containers and confirm no source writer remains.
   Keep the PostgreSQL server running: the command takes a logical dump, not a
   copy of live PostgreSQL data files.

The command independently refuses active attempts, unreleased resources,
interactive/processor allocations, receiving uploads and in-progress deletion.
It takes exclusive state and database maintenance locks; updated launchers and
release-writing commands cannot run concurrently. It also checks dispatcher and
processor locks and holds write-blocking locks on all queue tables while exporting
the SQL snapshot and copying files. File changes during copying fail verification.
This is not a substitute for stopping unrelated/operator processes that can write
to private storage.

Run commands as the account owning the private files. Source state, external
prepared directories, backup parents and restore parents must be private mode
0700, owned by that account, without symlink ancestors. Links, hard-linked files,
devices and pipes are rejected. Successful uploads, prepared receipts and retained
successful outputs are checked against their recorded identities before backup.

## Create and verify a laptop backup

```bash
cd ~/git/SimPathsWeb/SimPaths
mkdir -m 700 -p "$HOME/simpaths-backups"
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.backup create \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --backup "$HOME/simpaths-backups/multirun-$(date +%Y%m%d-%H%M%S)"
```

The source defaults to `~/simpaths-multirun-local`, pool `simpaths-local`, schema
`jasmine_batch`. Use `--state`, `--pool` and `--schema` for another existing source.
The command inspects the existing local database; it does not start/create one.
If native PostgreSQL clients are absent, it uses the matching clients inside that
existing loopback PostgreSQL container. No new container or image is created.

Replace the example name below with the directory actually created:

```bash
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.backup verify \
  --backup "$HOME/simpaths-backups/multirun-YYYYMMDD-HHMMSS"
```

Verification needs neither PostgreSQL nor Docker. It checks the dump hash, exact
file inventory, contents, permissions and retained releases. It detects corruption,
missing files and added files. Publication uses an atomic rename into a new name;
an existing destination is never overwritten. A failed create removes its partial
copy and leaves source jobs/files unchanged. Restart the original launcher after
backup and check normal readiness.

## Create a VM backup

From the reviewed SimPaths checkout, as the service account:

```bash
/opt/simpaths-online/venv/bin/python -m deploy.multirun.backup create \
  --config /etc/simpaths-online/multirun.toml \
  --backup /protected-backup-parent/multirun-YYYYMMDD-HHMMSS
```

The parent must already exist privately. Install `pg_dump` and `pg_restore` matching
the PostgreSQL server major version, or add `--client-container CONTAINER_NAME`
for the actual database container selected by a loopback DSN. Remote/certificate
connections require native clients. Passwords are supplied through the existing
private connection file/environment, not command arguments or printed reports.

All commands accept `--json`, returning counts, operation and success/failure in
`simpaths.multirun.backup.v1`. Exit status is 0 for success and 2 for failure.
The manifest itself contains private paths and filenames; it is not a public
operator-status report.

## Restore into an isolated target

Use the hosting code/schema version that created the backup and the same PostgreSQL
major version. Retain those code/build identities with the deployment artifacts.
To recover an older schema, restore and verify using its matching code first;
subsequent reviewed application updates use the usual checksummed migrations.
Cross-version schema conversion is not part of this restore command. Provision a
**separate, empty database** without starting an application against it. Create a
mode-0600 DSN file owned by the restoring account, pointing only to this target.
Do not edit the live source connection file. The commands do not provision roles,
databases or Docker resources.

The restore role needs ownership/creation rights for the target schema and access
to `pg_catalog.pg_control_system()` to bind interrupted restoration to the actual
cluster/database identity. If necessary, a PostgreSQL administrator can grant
`EXECUTE` on that function temporarily and revoke it after activation. This is an
operator restore privilege, not a new requirement for the running application.
Restored objects are owned by the restoring role (`--no-owner`, `--no-privileges`);
arrange the normal application role's access before serving the target.

For a backup from the default laptop pool:

```bash
cd ~/git/SimPathsWeb/SimPaths
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.backup restore \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --backup "$HOME/simpaths-backups/multirun-YYYYMMDD-HHMMSS" \
  --state "$HOME/simpaths-multirun-restored" \
  --dsn-file /path/to/private/restore-postgres.dsn
```

Add `--client-container CONTAINER_NAME` when using container clients. For a native
VM, use `--config /etc/simpaths-online/restore.toml` instead of explicit settings:
prepare a separate reviewed TOML pointing to the new state/DSN, preserving the
backup's pool ID. Disable notice delivery and automatic cleanup for initial checks.
Explicit settings and `--config` cannot be mixed.

The target state must not exist or overlap the backup or original source roots.
The database must be empty. Restore refuses to overwrite an existing service.
It copies files, uses single-transaction native `pg_restore`, verifies every table
row and file, and writes an inactive `restore-pending.json` marker. Launchers and
release changes refuse that marked directory.

Only these relocation changes are allowed:

- Registered prepared locations move to the new state or to its private
  `.restored-inputs-<backup-id>/` directories; the imported-training path list follows.
- The target's idle executor binding is cleared. Its next worker binds the new
  host/root before any dispatch. Original attempts/history remain unchanged.

Frozen specifications, fingerprints, seeds, budgets, owners, approvals, outboxes
and retention deadlines are preserved. Restore does not give files new retention
periods or add execution attempts. Existing secrets are copied without changing
the authentication/cookie design.

### Resume an interrupted restore

Run the same restore command with `--resume`. It requires the same verified backup,
inactive target marker and database identity. Partial file copying is redone;
database restoration is all-or-nothing. A crash after the dump commits is recognised
by the complete row hashes, so recovery does not blindly restore it a second time.
The target remains inactive on any failure. Do not remove the marker to bypass a
failed check or use `--resume` for a live directory.

### Activate after verification

Keep a restore exercise isolated. **Before activation, stop/isolate the original
service and its model containers** so two copies cannot execute the same queued
jobs. If recovering from a backup older than the last live activity, reconcile
any later work and externally delivered notifications before enabling the target.
Database rollback cannot undo a model launch or email already sent elsewhere.

```bash
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.backup activate \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --backup "$HOME/simpaths-backups/multirun-YYYYMMDD-HHMMSS" \
  --state "$HOME/simpaths-multirun-restored" \
  --dsn-file /path/to/private/restore-postgres.dsn
```

Activation rechecks all files/rows and requires every recorded Docker image ID to
be installed. It removes only the inactive marker; it starts no process. For the
laptop launcher, explicitly select the restored database:

```bash
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.local_web serve \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --state "$HOME/simpaths-multirun-restored" \
  --dsn-file /path/to/private/restore-postgres.dsn \
  --console-codes
```

Omitting `--dsn-file` would select the laptop launcher's usual state-specific
database rather than this explicitly restored target. For the VM, start the
service using its reviewed target TOML. Check sign-in, ownership isolation,
permitted output/input downloads, denied provider raw downloads, retained model
versions and Visualiser access. Enable delivery/expiry only after recovery review.

## Protect backups and test recovery

Backups contain raw/private data, email identities, diagnostic files and the
session secret. Store them outside source checkouts and web/static directories,
with restricted access and encryption on separate storage. This command supplies
private copies and checksums, not encryption or a backup scheduler. Hashes detect
damage, not authenticity: restore only trusted operator backups. Define off-machine
copying, access auditing and backup expiry, including how deleted data ages out.

The focused automated proof uses disposable PostgreSQL, native dump/restore and
fictional files. It checks write/maintenance exclusion, exact data/file recovery,
external-input relocation, interrupted restoration, image checks, existing-target
refusal, queued-job recovery and owner/provider download permissions. It sends no
real mail and runs no scientific simulations:

```bash
cd ~/git/JAS-mine/JAS-mine-web
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --test-pattern test_backup.py \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/backup_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements.txt"
```

All 55 focused checks passed across `postgres-queue-20261003-155508` (eight generic
database cases) and `postgres-queue-20261003-160608` (47 restore/deployment cases,
no skips). The successful rerun confirms disposable-resource cleanup. Earlier
restore-verification formatting/path-mapping errors were corrected; the failed
copies remained inactive. Local validation also passed 47 runnable file, release,
operator and configuration checks. These are fictional-data recovery checks,
not a production VM recovery exercise. Repeat restoration on the chosen staging
VM with its actual storage, role privileges, deployment artifacts and isolation
before accepting production recovery.
