<!-- (C) Copyright 2026, by Ross Richardson

Verified remote delivery, restart-safe local archive disposal and laptop recovery proof.
@author ross richardson
-->

# Local backup space after verified external delivery

The scheduled runner supports an explicit `local_retention="remote-only"`
policy. It creates a full compressed, encrypted archive locally, verifies the
uploaded archive and checksum receipt at the configured remote destination, then
removes the local archive. The source application, simulations, installed services,
filesystem allocations and remote retention are not changed by selecting a policy.

The default remains `local_retention="retain"`. Nothing is enabled or installed
by the source/example changes. Keep that default until the actual external
destination, independently held recovery key and isolated restore have been proven.
The external service needs its own history/expiry policy and monitoring.

## Configuration

In the private scheduled-backup TOML:

```toml
[backup]
# Other existing backup settings remain required.
local_retention = "remote-only"
keep_days = 30
keep_latest = 3
```

A complete pinned `[remote]` destination is required; local-only configurations
cannot select this mode. No zero-value age/copy limits are introduced. In
`remote-only` mode those limits retain the small completed-job/checksum/delivery
records; they no longer retain the verified archive payload. Pending jobs are
never retired by those limits. The remote archive and receipt are not deleted.

The next scheduled run reads the policy. Changing only local retention does not
change the source/destination identity or require recapture of a pending job.
Ordinary simulations and submissions continue; no model/image rebuild is needed.

## Exact removal conditions

1. Capture and encryption finish with the ordinary verified local receipt.
2. The configured transport confirms successful archive **and receipt** delivery.
   Production SFTP reads bytes back to compare length/SHA-256 before publication,
   recovers identical already-published copies and refuses conflicting content.
   A mere successful socket connection or upload command is insufficient.
3. A private `delivery.json` binds the encrypted receipt to the job and configured
   source, encryption key and remote destination. The scheduler durably records
   completion before starting local disposal.
4. A durable removal intent precedes unlinking the archive. An interrupted unlink
   or metadata acknowledgement is replayed idempotently on the next run.
5. The small original encrypted receipt, delivery/removal journal and completed
   job record remain locally until their metadata-retention policy allows removal.
   Status exposes the policy and `latest.local_archive_removed_at`.

No additional whole-archive hash pass is added solely for disposal of a newly
verified delivery. File ownership/size/mode, links and receipt consistency are
checked under the existing exclusive scheduler file lock. Older completed remote
backups without a delivery journal must be reverified at their actual destination
before the new policy can remove them. Other source/key/destination scopes remain
untouched and need a separately reviewed cleanup if retired.

Previously delivered payloads are cleaned before a new snapshot is captured.
Reverting to `retain` keeps future payloads; it does not download removed archives.
A removal intent already made durable is completed after restart, even if the
policy has since reverted. Remote verification records remain bound to their
original destination; changing a destination while a job is pending is rejected.

## Failures and space

- Upload failure, corrupt/partial readback, receipt-transfer failure or an
  unconfirmed transport result preserves the exact local archive and its pending
  job. Retries do not run simulations or recapture a newer backup.
- A pending remote outage blocks creation of another scheduled snapshot. Local
  pending archives therefore do not accumulate for every timer tick, but the
  verified backup becomes older. Existing backup-failure/overdue monitoring applies.
- Local cleanup failures are reported as `local-cleanup`, with a durable incident
  and the existing opt-in operator failure/recovery notices. They prevent another
  scheduled capture from consuming staging space until maintenance succeeds.
  Alerts include safe incident metadata only; no raw data, paths or credentials.
- Small journal/receipt files remain on disk. Retain trusted checksums independently
  of the source VM too; local metadata is not an independent recovery route.

Allow space for **one full compressed encrypted archive**, the SQL dump, the
largest temporary SingleRun export ZIP, metadata and headroom. This is not an
arbitrarily small network buffer. Compression is workload-dependent; measure a
complete capture's peak before reducing the installed backup filesystem. Explicit
directory captures and legacy plaintext jobs still have their larger requirements.

## Local proof without an external account

The focused local regressions use fictional files and the real SFTP publication
algorithm backed by a simulated destination. They exercise outages, changed bytes,
partial uploads, receipt failure, durable completion before deletion, crashes
before/after unlink, metadata-write failures, linked files, policy switches,
legacy deliveries, captured alerts and small-record retention.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
PYTHONDONTWRITEBYTECODE=1 ~/simpaths-browser-tests/venv/bin/python \
  -m unittest deploy.multirun.test_backup_remote_retention.RemoteRetentionTests -v
```

For the native recovery proof, run outside quiet performance benchmarks with
networking available for isolated test dependencies. It uses disposable
PostgreSQL, temporary GnuPG keys, actual encryption/decryption and file/SQL
restoration. The remote destination is simulated; no external backup account,
installed service, scientific simulation or real email is used. Existing stream,
legacy, SingleRun and retained Visualiser backup checks run alongside the new cases.

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --proof-only \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/backup_stream_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements-backup-proof.txt" \
  --output "/tmp-codex/backup-remote-retention-native-$(date +%Y%m%d-%H%M%S)"
```

The new native cases retrieve ciphertext and its receipt from the simulated
destination after local removal, decrypt them with an independent temporary
keyring, and verify/restore the real PostgreSQL/filesystem recovery point. They
also verify that an outage retries the same native encrypted snapshot.

This establishes application-policy and archive/recovery compatibility. The actual
provider still needs SSH/SFTP feature, host-pin, interruption, independently
retrieved restore, remote history and peak-space acceptance on the deployed host.

## Qualification — 22 October 2026

All **74 local file/scheduling/transport checks passed**, with no failures,
errors or skips (9.161 seconds). This includes the 22 focused retention-policy
cases. Source hashes and local results are retained in
`/tmp-codex/backup-remote-retention-local-20261022-145859/report.json`.

The native laptop proof then passed **all 94 checks**, with no failures, errors
or skips (86.637 seconds), and confirmed disposable-database cleanup. The two new
native cases restore files/database data after local disposal and retry the same
real encrypted snapshot after a simulated destination outage. Evidence:
`/tmp-codex/backup-remote-retention-native-20261022-155734/report.json` and its
`model-proof/report.json`.

No external account, real email, scientific simulation, installed service,
filesystem resize or production policy change was involved. The default stays
`retain`; actual external-provider acceptance remains outstanding.
