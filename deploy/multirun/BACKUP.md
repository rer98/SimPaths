<!-- (C) Copyright 2026, by Ross Richardson

Offline/online shared-service recovery, encryption, scheduling and safe restoration.

@author ross richardson
-->

# MultiRun backup and restore

`python -m deploy.multirun.backup` saves a matching PostgreSQL snapshot and private
files. Restore uses a **new state directory and a separate empty database**. It
verifies both and leaves the copy inactive until an explicit activation command.
Capture/verify/restore/activate never starts simulations or sends email. The separate
scheduled runner can send explicitly enabled operator alerts and retire its own old
completed encrypted copies.

Choose the appropriate mode:

| Mode | Source during capture | Recovery coverage |
| --- | --- | --- |
| Default offline (`v1`) | Maintenance window; no active or unreleased work | Exact MultiRun state/files, with approved path relocation |
| `create --online` (`v2`) | Simulations and ordinary PostgreSQL writers continue | Immutable MultiRun inputs/settled output, interrupted-attempt records, and shared SingleRun registries/saved exports |

Both require one queue schema containing one resource pool. Online capture includes
SingleRun schemas in the **same PostgreSQL database** durably bound to that pool.
Independent SingleRun databases are outside this command's scope. Neither mode
provides an in-memory Java/H2 checkpoint or resumes an interrupted scientific run
from its last simulated year. Existing offline safety checks remain in place.

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

## Prepare an offline maintenance window

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
`simpaths.multirun.backup.v1` or `v2`. Exit status is 0 for success and 2 for failure.
The manifest itself contains private paths and filenames; it is not a public
operator-status report.

## Capture while simulations continue

Use the updated hosting code on every source launcher/worker. Online mode relies
on its deletion/control fences; an older launcher or an unrelated operator process
writing/deleting private files cannot participate in this protocol safely.

```bash
python -m deploy.multirun.backup create --online \
  --config /etc/simpaths-online/multirun.toml \
  --backup /protected-backup-parent/online-YYYYMMDD-HHMMSS \
  --minimum-free-gib 1 --json
```

For the laptop, use the frontend/state/pool/schema options from the earlier example
instead of `--config`, adding `--online`. Keep the source launcher and PostgreSQL
running. Capture retains immutable files against deletion, locks release updates,
and exports a repeatable-read database snapshot. Native `pg_dump --snapshot` uses
that exact snapshot. Ordinary queue transactions, uploads/new publication, model
execution, chart/status reads and existing downloads continue. Housekeeping defers
physical deletion; an explicit deletion request receives a retryable conflict.

Shared SingleRun capture temporarily fences Java mutations and container removal
with a separate nonblocking PostgreSQL advisory lock. Start/Pause/Reset/uploads
should be retried after capture; the running engine is not paused. Its status/chart
reads and activity updates continue. Capture checks the reviewed VM schema,
deployment/session/model labels, private network and immutable runtime image before
reading a container. A busy fence or unavailable/changed retained source fails
without publishing a partial recovery point.

Online copies include snapshot-ready uploads, verified prepared datasets, finished
attempt receipts, retained CSVs and each run's `input/options.txt`. Large repeated
run-input copies, unconfirmed/active workspaces, receiving uploads and caches are
excluded. An output whose deletion was requested before the snapshot stays
unavailable after restoration; restoration does not resurrect it.

Shared SingleRun copies include the full registry/security tables, current settings,
original text/workbook inputs with private source mappings, immutable image IDs,
and permitted saved output ZIPs. The newest export is excluded when the engine is
running. Paused exports are preserved as saved files and may be scientifically
partial; the backup does not label them completed. Closed export trees and original
inputs are checked for changes before/after streaming. A live input H2 database is
excluded; an already closed saved export can contain its original input copy.
The source API's detailed-output denial remains recorded in the recovery copy.

Use a **separate backup filesystem** so copying cannot consume the live model's
reserved working space or fill Docker/PostgreSQL storage. Reserve room for the
verified plaintext snapshot, encrypted copy and SFTP readback copy. Per-file space
checks retain `minimum_free_gib` (default 1 GiB); this is a safety floor, not a disk
quota or a shared reservation. Size it and the backup volume from measured peaks.
Capture is bounded by file/metadata limits; a long exported snapshot can delay
PostgreSQL vacuum reclamation. Test responsiveness and capture duration with the
chosen staging volume and representative data before enabling the timer.

### Restoring an online recovery point

Use the same isolated-target commands below. Online restoration additionally:

- Settles only snapshot-interrupted attempts as recovery interruptions. Cancellation,
  recorded revocation/time limits, retry opt-out, attempt caps and cumulative budgets
  keep their existing meanings. Eligible transient interruptions retry normally;
  other outcomes require review. Partial scientific output is never published.
- Charges execution only through the snapshot time, preserves attempts already
  spent, retry credits, frozen seeds/settings and existing retry delay, and releases
  only the isolated target's obsolete model/processor/interactive reservations.
  Time spent offline or awaiting operator activation is not execution time.
- Marks snapshot-receiving uploads failed in the target; source uploads are untouched.
- Converts shared SingleRun sessions into private saved-export records, without
  live endpoints/credentials or a container reservation. The restored deployment
  gets a distinct executor identity so it cannot adopt/remove source containers.
  Existing ownership cookies remain usable when the original configured signing
  secret is retained; current model authorisation is checked on every download.
- Provides the original `/sim/<session-id>` page with saved exports/settings and a
  link to launch a fresh session. Saved recovery files expire 30 days after capture;
  later backups/restores preserve that deadline. Cleanup removes only these private
  recovered files/records, with no source-container removal. An old backup may
  already have expired saved exports when restored. A download accepted before
  expiry retains the verified file until its transfer ends; later requests fail.

Activation of `v2` requires **`--source-isolated`**, confirming that the original
services and models cannot execute concurrently with the target. It also checks
the original state's maintenance lock when that directory still exists locally.
The flag cannot stop another host: isolate it operationally first. Restore gates
also prevent a separate shared SingleRun launcher starting against an unfinished
target. No source session signing secret, cookie policy or launcher secret-generation
design changes are introduced. Retain SingleRun's configured signing secret/catalogue
separately with the other deployment artifacts.

## Scheduled encryption and protected off-machine copying

The provider-independent runner is `python -m deploy.multirun.backup_schedule`.
Templates in `vm/backup.toml.example`, `vm/simpaths-backup.service` and
`vm/simpaths-backup.timer` are **not installed or enabled** by this work.

1. On a separate trusted recovery machine, generate/protect an OpenPGP encryption
   key and test recovery. Retain its private key, passphrase and trusted fingerprint
   outside the VM. Install only an exported public key on the VM. Pin the complete
   primary-key fingerprint in a mode-0600 operator configuration. GnuPG uses an
   isolated temporary public keyring, explicit options and no automatic key discovery.
2. Install a reviewed private TOML from the example. Config/SSH private-key/host-pin
   files must be service-owned and private, without links. A public key may be
   root/service-owned and read-only to other accounts. Config parents may be
   root-managed; backup storage itself is service-owned 0700.
3. Select a separate protected backup host later. The optional `[remote]` section
   uses pinned-host OpenSSH SFTP with explicit identity, no agent/forwarding and
   strict host-key checking. Verify the host key independently before installing
   the private `known_hosts` file. The destination must support SFTP hard links.
   Remove `[remote]` until configured; local encryption alone is not off-machine
   disaster protection.
4. Set operator alerts explicitly with `[alerts] enabled=true`, an operator-only
   recipient and the reviewed SMTP environment. The default is disabled. Messages
   contain an incident ID and stage, without user identities, raw data, paths,
   logs, DSNs or attachments. Stable message IDs and durable pending notices support
   retry; SMTP acknowledgement loss can still produce duplicate deliveries.
5. Manually run and inspect the configured protection before installing/enabling
   the reviewed systemd templates. The timer checks hourly, with jitter; capture
defaults to once per 24 hours. Retry eligibility defaults to 30 minutes and is
   acted on at the next timer check. The service uses low CPU/I/O priority, a 512 MiB
   memory ceiling and a two-hour process timeout, without changing model policies.

An interrupted upload can leave a read-only remote `.partial` file because OpenSSH
creates it with the local ciphertext's permissions. A retry removes that incomplete
staging file and retransmits the same verified encrypted snapshot; it does not
recapture simulation data. Readback/checksum verification and atomic publication
still precede success, and a conflicting completed backup is never overwritten.

```bash
python -m deploy.multirun.backup_schedule run \
  --config /etc/simpaths-online/backup.toml
python -m deploy.multirun.backup_schedule status \
  --config /etc/simpaths-online/backup.toml
```

The private durable journal separates capture, encryption and transfer. A failed
transfer retries the **same verified encrypted snapshot**, without recapturing data
or restarting simulations. Source identity includes the resolved VM settings and
database endpoint, so editing a DSN/TOML at the same path cannot make an old pending
snapshot count as protection for another source. Credential-only rotations do not
change that identity. Finish pending work before changing the source or destination.
The plaintext directory is erased only after a complete
encrypted local file and checksum receipt exist. An interrupted encryption can be
repeated from that verified plaintext. Abrupt interruption is recovered by the next
runner; every stage remains unpublished/pending until its checks pass.

Only ciphertext and its small receipt leave the VM. SFTP uploads under a partial
name, reads the encrypted bytes back to verify SHA-256, then creates the final
name atomically without replacing an existing completed file. Lost publication
acknowledgements are resolved by checking identical remote bytes. This readback
costs another transfer and temporary local copy. A conflicting/corrupt file fails
protection; no overwrite fallback is used. Receipt delivery must finish too.

`status` returns nonzero when there is no verified backup, protection is overdue,
or backup work has failed. Include this in an independent host-monitoring check:
email from this VM alone cannot alert if the VM, timer or configuration is broken.
The runner retries failed mail and sends one incident-resolution notice when
protection recovers. Status/terminal errors disclose only safe stages/IDs.

Local retention defaults to 30 days and at least three completed encrypted copies.
Only this runner's completed job directories are retired; pending work and the
newest retained copies survive. These minimum-copy rules can retain a backup longer
than 30 days during an outage. Define expiry/access/auditing on the separate host
before deployment; this runner deliberately does not delete remote backups. A
successful SSH transfer proves byte recovery, not protection from a compromised
VM credential: configure a restricted backup account and independent retention or
immutable snapshots on the storage host. Ciphertext checksums are not signatures;
retain trusted receipts outside the VM and restore only trusted operator backups.

### Recover an encrypted bundle off-machine

Use the trusted ciphertext checksum from the independently retained receipt and
a private recovery keyring containing the decryption key:

```bash
python -m deploy.multirun.backup_schedule unseal \
  --encrypted /private/recovery-BACKUP_ID.tar.gpg \
  --expected-sha256 CIPHERTEXT_SHA256 \
  --keyring /private/recovery-keyring \
  --destination /private/new-verified-backup
```

Decryption streams into private staging, rejects links/traversal/special files,
checks GnuPG integrity and the complete backup manifest, then publishes into a
fresh name. Failure publishes no plaintext backup. Continue with verify/restore
and activation as below, adding `--source-isolated` for an online recovery point.
The private decryption key is never required on the source VM.

The native snapshot mechanism follows PostgreSQL's
[exported-snapshot documentation](https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-SNAPSHOT-SYNCHRONIZATION).
Encryption and transfer use the installed
[GnuPG CLI](https://www.gnupg.org/documentation/manuals/gnupg26/gpg.1.html) and
[OpenSSH SFTP](https://man.openbsd.net/sftp.1); no custom cryptography is introduced.

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

For offline backups, only these relocation changes are allowed (online recovery
also performs the interruption conversion described above):

- Registered prepared locations move to the new state or to its private
  `.restored-inputs-<backup-id>/` directories; the imported-training path list follows.
- The target's idle executor binding is cleared. Its next worker binds the new
  host/root before any dispatch. Original attempts/history remain unchanged.

Frozen specifications, fingerprints, seeds, budgets, owners, approvals, outboxes
and retention deadlines are preserved. Restore does not renew MultiRun retention
periods or add execution attempts. Existing secrets are copied without changing
the authentication/cookie design.

For native `dedicated_storage=true` targets, install the
[workspace quota broker and static launcher](WORKSPACE_QUOTAS.md) for the new
execution/artifact roots before activation. Use a fresh destination ledger and
unused project-ID range. Activation with the target `--config` rebuilds quotas
from verified frozen attempt allowances after file/row/image checks. A quota
failure leaves the inactive marker in place; repeating activation resumes the
same allocations. Portable backups do not copy filesystem project IDs. The
initial inactive copy still requires a correctly sized finite destination volume.

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
with restricted access and encryption on separate storage. Use the scheduled
encryption/transfer commands above for routine protected copies. Hashes detect
damage, not authenticity: restore only trusted operator backups. Define off-machine
access auditing and backup expiry, including how deleted data ages out.

The focused automated proof uses disposable PostgreSQL, native dump/restore and
fictional files. It checks offline write/maintenance exclusion, online concurrent
writes, exact data/file recovery,
external-input relocation, interrupted restoration, image checks, existing-target
refusal, queued-job recovery and owner/provider download permissions. It sends no
real mail, contacts no backup host and runs no scientific simulations. Native
encryption checks use a newly generated disposable GnuPG key, never a user keyring:

```bash
cd ~/git/JAS-mine/JAS-mine-web
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --test-pattern test_backup.py \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/backup_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements.txt" \
    requirements-vm.txt
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

Expansion validation: all **122 checks passed** in
`postgres-queue-20261003-191224`: ten generic database cases and 112 hosting/shared
SingleRun/encryption/HTTP cases, without failures, errors or skips. The disposable
database was removed. Thirty local file/capture/scheduler/transfer cases also
passed; the eight scheduler cases passed again after adding resolved-source and
credential-rotation coverage. Transfer protocol
faults use an isolated SFTP fake; the selected backup host, its key pin, account,
atomic publication, independent retention and monitoring still require staging
acceptance. No timer, backup host or real alert delivery has been enabled.

### Local native scheduled-backup rehearsal

Before selecting a VM or backup provider, run the isolated transport/recovery
rehearsal from an ordinary laptop terminal with Docker and its user systemd
manager available:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
PIP_DEFAULT_TIMEOUT=60 ~/simpaths-browser-tests/venv/bin/python deploy/acceptance/run_backup_rehearsal.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web"
```

The enclosing runner creates a disposable PostgreSQL container and isolated Python
dependencies. The rehearsal builds/reuses a small OpenSSH image, publishes only on
IPv4 loopback, and creates transient user timer/service units. No unit is installed
or enabled. Host SSH configuration, known-host files, keys, machine clock, operator
configuration and real databases remain untouched. Only fictional inputs/results,
new SSH/OpenPGP keys and a private test configuration are used; alerts are disabled.
Eight MiB of incompressible fictional upload bytes make a partial transfer observable
without a scientific simulation. A local relay passes encrypted SSH bytes at a
bounded rate; it does not replace the SFTP protocol or decrypt traffic.

The timer invokes the existing `backup_schedule run` command in fresh processes.
It retains the template's oneshot execution, 512 MiB/128-task limits, low CPU/I/O
priority, private umask, two-hour timeout and process-group termination. Test ticks
use one second initially and five seconds after each exit, with no calendar/jitter;
installed-host filesystem/kernel hardening is separate acceptance. The configuration
keeps the normal 24-hour capture interval but uses the supported one-minute retry
delay. That delay elapses normally: neither the clock nor the durable journal is
edited to bypass it. A synthetic active attempt has a thirty-minute fixture allowance;
no production model/runtime policy is changed.

The rehearsal checks:

- Real public-key authentication and host-key pinning; wrong keys/pins and reads
  outside the container's SFTP chroot are denied. The server receives no source
  files, DSN, client private key or OpenPGP recovery key.
- Native online PostgreSQL capture and GnuPG encryption while the fictional model
  continues file writes and heartbeats. Mutable output is excluded; the only remote
  payloads are ciphertext and its fixed transfer receipt.
- SIGKILL during a partial upload, a real SFTP outage, and later timer retries of
  the same snapshot. Pending protection is not counted as success. Readback/hash
  verification and atomic hard-link publication run through OpenSSH; later timer
  checks do not capture another snapshot inside the daily interval.
- Duplicate publication, rejection of conflicting completed bytes, independent
  SFTP retrieval, a trusted-checksum failure, native decryption/dump restoration
  and resumable inactive verification. Original inputs, secrets, seed/runtime/retry
  policies and access rules survive; live model memory/output is not reconstructed.
- Existing owner-cookie access through restored HTTP routes, anonymous/other-owner
  denial and continued denial of raw provider downloads. Image availability checks
  use an explicit fictional-image fixture; restored execution/mail are never started.

Evidence is written under `~/simpaths-benchmarks/backup-rehearsal-*/`, including
private scheduler/SFTP/supervisor logs and both reports. Cleanup stops/removes only
the owned transient units/container, disposes temporary keyrings and files, and
checks removal of the fixture schema and separate restore database. The enclosing
runner removes its disposable PostgreSQL container. The reusable SFTP image remains
cached. Failed cleanup leaves the fixture private and reports failure.

Implementation validation on 4 October: all **50 focused local checks passed**
without failures, errors or skips, plus all **seven native rehearsal stages** in
`backup-rehearsal-20261004-183630`. Both reports confirm success and cleanup.
The timer captured one 8,424,741-byte encrypted snapshot while the fictional writer
continued. After SIGKILL at a 261,120-byte read-only partial and a real SFTP outage,
fresh timer processes retried the same ciphertext after the configured delay.
The restarted endpoint changed port 32908 to 32909; reconnection retained the
original host-key pin. Readback/checksum verification and atomic publication passed,
and later timer checks did not recapture the source. The journal recorded 15
invocations: 12 expected unsuccessful checks during interruption/backoff and three
successful checks, with one captured snapshot.

Independent SFTP retrieval and GnuPG decryption restored and verified **20 files
and 35 database tables**. Restore remained inactive until explicit fixture
activation, omitted live output and preserved inputs, seeds, policy and the
original attempt count. Resume verification passed without changing restored rows.
The original owner cookie worked on restored HTTP routes; anonymous/other-owner
access and raw provider downloads remained denied. The source attempt was unchanged;
the writer recorded 580 heartbeats. Temporary units, container, keyring, files,
schema and restore database were cleaned up; the enclosing runner removed
PostgreSQL. The reusable SFTP image remains cached.

This completes the local rehearsal. The SFTP endpoint is a separate container on
the same machine/filesystem, and the report retains `production_acceptance=false`.
Off-machine disaster protection, the chosen remote account/key pin, independent
remote retention/monitoring, production hardening, load and recovery throughput
remain deployment acceptance.

#### Earlier debugging runs

The first native attempt, `backup-rehearsal-20261004-121405`, passed all 36 local
checks and confirmed cleanup, but stopped during image construction because
Debian already provides the account name `backup`. The fixture now creates and
allows only `simpaths_backup_test`; package installation has a separate cached
build layer.

The second attempt, `backup-rehearsal-20261004-144329`, passed real OpenSSH
authentication, host-pin and chroot-denial checks and reached a partial encrypted
upload, then stopped at the live-capture assertions. Both reports confirm cleanup.
The harness had discarded one scheduling rule because `systemctl show` emits a
separate `TimersMonotonic` line for each rule. It now preserves both lines, checks
the exact initial/repeat intervals and retains effective properties and failure
locations in the private report. All 39 focused checks pass, including this
regression and rejection of relaxed resource/security limits. At that stage, the
remaining native interruption, retry, retrieval and restore checks were pending.

The next run, `backup-rehearsal-20261004-163901`, passed applied timer limits,
live capture and SIGKILL/outage recovery of the same 8,424,778-byte ciphertext.
It timed out reconnecting to the restarted SFTP fixture before the one-minute
retry deadline; both reports confirm cleanup. The fixture had cached Docker's
ephemeral host port across restart. It now rediscovers and validates the owned
loopback port, updates the relay's destination and retains the client address,
host-key pin and backup configuration. Separate reconnect/delay/verification
phases and port observations are recorded. All 42 focused checks pass, including
changed-port routing and rejection of public/privileged ports or changed mounts.
The remaining stages were pending until the successful run recorded above.

Run `backup-rehearsal-20261004-164735` confirmed that Docker changed port 32902 to
32903 and that the relay reconnected using the original host-key pin. The scheduled
retry then remained pending; both reports confirm cleanup. Local regressions
reproduced a transport bug: an interrupted read-only upload could not be overwritten
before the post-upload chmod. The SFTP copier now removes only the old staging file
before retransmission. Regression cases cover ciphertext and receipt partials,
denied cleanup, unchanged local ciphertext and preservation of completed bytes.
All 45 focused checks pass. The harness also records partial modes, final scheduler
and unit state, and remote file sizes/modes/link counts before cleanup. Native
completion was pending until the successful run recorded above.

Run `backup-rehearsal-20261004-171527` confirmed a read-only `0400` partial and
successful retry of the same 8,424,782-byte ciphertext. Native remote readback,
checksum verification, private `0600` final files and hard-link publication passed;
later timer ticks retained one capture with no pending incident. The run then
failed while stopping its transient units: the completed service had already been
collected, and `systemctl stop` reported it as missing. The fixture now stops the
owned timer first, accepts verified absent units and rechecks the service before
stopping it. Failed stop commands remain fatal unless subsequent inspection proves
the unit absent; surviving processes and unrelated/persistent units are rejected.
The scheduler evidence is recorded before this separate shutdown phase. All 50
focused checks pass, including collected-unit races and genuine shutdown failures.
The original inner report records failed cleanup; its final unit inspection showed
both units absent, the SFTP container/keyring were removed, and the enclosing runner
removed PostgreSQL. The retained fictional work directory was subsequently removed
after checking those reports and its owned private paths. Original evidence remains
unchanged. The following successful run completed retrieval, native restore and
cleanup as recorded above.
