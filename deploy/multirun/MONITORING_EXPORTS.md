<!-- (C) Copyright 2026, by Ross Richardson

Administrator monitoring archives, queued mail, recovery and offline collation.

@author ross richardson
-->

# Administrator monitoring exports

The private service can preserve complete new monitoring records and changed
summaries, pack them hourly, and email numbered compressed attachments daily.
The administrator can combine saved attachments into a SQLite record and labelled
HTML/CSV views. The ordinary 2,000-row report remains a bounded recent view.

This is host Python code plus additive JAS-mine-web migration **032**. It requires
the updated application release and database migration, without changing the model
images, JARs or scientific settings. Deploy through the documented update/backup
procedure; existing older application versions reject the additional migration.

## Configuration and live controls

The new `[monitoring_exports]` section is optional and defaults to disabled.
For the initial monitored deployment, enable archival and, after SMTP/recipient
verification, delivery in the private VM configuration:

```toml
[monitoring_exports]
enabled = true
mail_enabled = true
archive_seconds = 3600
delivery_seconds = 86400
journal_mib = 64
journal_rows = 100000
archive_mib = 512
archive_days = 30
part_mib = 2
```

Mail also requires `[notifications].enabled = true`, its private `admin_email`,
and the existing SMTP environment. Recipients and credentials never appear in the
attachments. There is no browser endpoint for changing recipients or exporting
these private measurements. The service seeds a private control file from TOML
only when that file is absent; a later restart preserves live overrides.

After installation, run controls as the private executor's Unix account, using
its existing application environment. Replace the example configuration path:

```bash
python -m deploy.multirun.monitoring_exports --config /etc/simpaths-online/multirun.toml enable --mail
python -m deploy.multirun.monitoring_exports --config /etc/simpaths-online/multirun.toml status
python -m deploy.multirun.monitoring_exports --config /etc/simpaths-online/multirun.toml mail-disable
python -m deploy.multirun.monitoring_exports --config /etc/simpaths-online/multirun.toml disable
```

Changes apply within about five seconds. They never restart, cancel or resize
jobs. `enable` accepts the settings above as hyphenated options, for example
`--delivery-seconds 43200 --archive-mib 1024`. Running it without `--mail` enables
preservation with delivery disabled. `mail-disable` retains preservation;
`disable` stops both new preservation and delivery, keeping the existing outbox.
Cadence changes affect subsequent cycles; an already scheduled delivery keeps
its due time. An explicit disable also works when a journal is full and keeps
the larger restored database budgets/data intact.
Collectors may then resume their ordinary pruning without archival protection.
Keep exports enabled while measurements need preservation. Enable exports before
collection starts; measurements already deleted cannot be recovered.

Existing external/Java monitoring controls are independent. The archive records
requested enabled/disabled settings and sampling intervals, with a daily control
heartbeat on quiet days. Java controls still apply only to new launches. This
archive does not turn Java instrumentation or other collection on itself.
These control/queue snapshots are sampled about once a minute; quick intervening
toggles are not a complete control audit. Existing resource samples retain their
chosen interval, including 15 seconds, and all persisted numeric observations
are journalled. At most 100 attempt contexts are paged per pass, prioritising
unfinished work and the most recent 10,000 attempts. Source summaries/windows
and missing observations remain distinct from that contextual metadata.

## Preservation, budgets and failure behaviour

1. PostgreSQL triggers copy each new observation and changed numeric summary to a
   journal in the **same transaction** as the write. Before deletion, a trigger
   also preserves any uncaptured latest version. Both age and row-cap pruning
   are covered. A stable revision orders late observations and corrections.
2. A separate private worker compresses a bounded prefix, checks its format and
   hashes, stores its numbered parts in the database, then reopens and checks the
   stored bytes before deleting that journal prefix. A crash rolls back the
   operation. Old detail can be pruned while mail is unavailable because the
   verified outbox is already durable.
3. The sender claims parts with expiring leases, releases database locks before
   SMTP, and acknowledges only its own lease. Failed delivery retries from one
   minute up to six hours; older deferred parts do not prevent the next day's
   newer parts from being sent. A crash after successful SMTP can cause a resend;
   stable part identities and the importer prevent double-counting.

Default budgets are **64 MiB/100,000 pending journal rows** and **512 MiB of
compressed outbox plus conservative metadata allowances**. These are logical
budgets, not filesystem quotas: PostgreSQL indexes, WAL and vacuum overhead also
need free space. Each packing transaction reads at most 2,000 revisions and about
4 MiB of journal data; that bound pages through the archive and never truncates
the overall export. Seed/backfill and context sampling are bounded separately.

When the outbox is full, it retains unsent parts and leaves the journal intact.
When the journal also fills, optional measurement writes and their pruning roll
back. Bounded collector buffers may eventually lose new optional observations;
the source records the blocked count and interval, and the importer marks the
coverage incomplete. Jobs continue: these hooks never execute in scheduler,
container or HTTP request transactions. No finite local budget can guarantee an
unlimited archive through an indefinite delivery/storage outage.

Delivered batches remain for 30 days by default; **undelivered batches are never
discarded automatically**. Increase the budget, repair delivery or independently
preserve the archive when status reports pressure. `status` shows retained bytes,
pending/failed parts, oldest pending time and error categories. Private
`performance-exports-status.json` additionally records the worker's last error.
No exception bodies or credentials are retained. Size/restart/collection failure
does not silently turn an incomplete record into a complete one.

## Email contents and size

The email contains readable summaries of preserved changes: successful/failed
attempt references, queue delays, observed working RAM, host pressure, completed
Visualiser preparation and cache reprocessing where measured. These summaries
describe changes in included batches, **not disjoint daily totals**. Use the
offline importer for combined totals and the latest cumulative snapshots.

Each attachment is `monitoring-<batch UUID>-part-NNNN-of-NNNN.json.gz`. Its manifest
includes format/units, source and epoch lineage, pool, revision range, record and
part counts, part-content checksums, and known blocked-collection intervals.
One message contains at most 32 attachments and **5 MiB compressed data**. MIME
encoding adds roughly one third, plus headers/body. Larger exports span several
messages; a multipart batch may span messages. Do not discard other numbered
parts after opening the first one. Actual daily volume depends on enabled
measurements, concurrent work and sampling interval; measure it on the VPS.

Only allowlisted numeric/categorical monitoring and opaque IDs are included:
observations, latest/versioned review and operational summaries, attempt timing
and resource metadata, and requested monitoring controls. There are no person
rows, scientific outputs, user names/emails, input values, seeds, credentials or
private paths. Provider/version fingerprints from existing monitoring remain
available. Treat these operational records and attachments as private.

## Offline collation

Save all gzip attachments into one folder. No VM connection, database credentials,
Docker or networking is needed. Use a local copy of the application code:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
~/simpaths-browser-tests/venv/bin/python deploy/multirun/monitoring_exports.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" merge \
  --folder "$HOME/simpaths-monitoring-mail" \
  --database "$HOME/simpaths-monitoring.sqlite" \
  --html "$HOME/simpaths-monitoring.html" \
  --csv "$HOME/simpaths-monitoring-versions.csv"
```

The same command can be run after each day's attachments are added. Duplicate
emails, overlapping batches and out-of-order complete batches are safe. Unique
observations use source-qualified immutable IDs; updated cumulative summaries
replace older revisions for current views and remain versioned in SQLite/CSV.
They are never repeatedly added. HTTP percentile bounds are recomputed from
deduplicated response counts, rather than averaged from daily percentiles.

`--since 2026-10-20T00:00:00Z --until 2026-11-01T00:00:00Z` selects a UTC range
(inclusive/exclusive). Observation charts/counts use observation time, attempt
views use submission time, and whole review/operational windows overlapping the
range remain labelled as cumulative. The CSV contains versions captured in the
range, using observation time for observation rows. SQLite retains all imported
history independently of server retention. Keep the original attachments too.

Missing numbered parts, known revision gaps, missing ancestors, conflicting data,
restored source forks, unsupported formats and known collection blocks are
reported. Exit status **2** means the combined record is incomplete or an input
was rejected; valid independent attachments can still be imported and inspected.
Exit status 1 is an operational/configuration error. A source fork's divergent
cumulative rows are excluded from combined current values until reviewed; the
tool does not guess which branch to trust. Independent installations retain
separate source IDs. Completeness refers to the imported batches and their known
lineage, not proof of continuous sampling or that no later email exists.

## Manual copies, backup and restore

To pack one bounded pending prefix without sending mail, use `pack` in place of
`status`. Repeating it drains additional prefixes. To copy already durable
attachments, use `dump --output /private/path/to/export-copy`. The destination must
be private; copies are verified and conflicting existing files are not replaced.
A dump does **not** acknowledge email delivery or authorise automatic deletion.

Journal, source/revision records and compressed outbox parts are ordinary tables
in the existing PostgreSQL schema, so offline and online full backups include
them. Restore verification checks their content along with the other tables.
The next enabled private worker creates a child epoch of the restored source;
history already imported is deduplicated, and divergent post-backup branches
remain explicit. Inactive restore/activation guards are unchanged. As with other
live controls, online backups omit the control file: verify the private VM
configuration and recipient, then explicitly enable delivery on the replacement.
Do not run two live installations against one source/history. Source/epoch
bookkeeping has a 10,000-epoch guard; it requires operator review if exhausted.

## Validation

The first native run passed **82 checks**, including 9 archive-specific tests:
`/tmp-codex/monitoring-exports-native-20261020-101157/report.json`.
The combined runtime/recovery follow-up passed **69 checks without skips**:
[`monitoring-exports-final-20261020-103414/model-proof/report.json`](/tmp-codex/monitoring-exports-final-20261020-103414/model-proof/report.json).
Both online/offline restores preserve pending changes and parts; restored
history deduplicates against already imported mail. The local review passed
**47 frontend tests** and **38 adapter/command/configuration tests**. These counts
overlap the native proof and must not be added as unique cases. The final
[16-check mail/control-pressure follow-up](/tmp-codex/monitoring-exports-mail-retry-20261020-104114/report.json)
passed, covering delayed older deliveries, next-day mail, full journals and an
explicit disable with larger restored budgets; cleanup was confirmed.
All tests use fictional measurements, captured senders and disposable state.

Real recipient/SMTP acceptance and daily export volume/storage cost remain VPS
deployment checks. Previous quiet monitoring trials predate the new journal/mail
worker; they do not measure this addition's overhead. Check its database time,
CPU, storage and web responsiveness under the actual mixed workload.
