<!-- (C) Copyright 2026, by Ross Richardson

Read-only SimPaths Online operator status, interpretation and disposable validation.

@author ross richardson
-->

# Operator status

Inspect an existing SimPaths Online service without starting its applications or
altering submitted work. This command provides terminal output and a versioned
JSON report. It uses the selected resource pool, verified retained releases and
the existing private state directories.
For consistent snapshots and isolated recovery, use the separate
[backup and restore commands](BACKUP.md).

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

### Live scheduler storage budget

The report includes private `storage_budget` policy/status, with the actual
database allowance in `database.resources.capacity.storage_mib`. Once enabled,
follow mode checks mounted filesystem growth every 30 seconds, preserving
headroom and any chosen ceiling. Fixed increases and freezing the current budget
also work without restarting model jobs. See [storage-budget controls](STORAGE_BUDGET.md)
for enablement, backup/recovery behavior and the disposable native proof.
Unsafe or unavailable measurements pause new batch admissions and appear as a
status warning; existing jobs retain their allocations and continue being polled.

### Live performance review

The separate [performance review](PERFORMANCE_REVIEW.md) records absolute sampled
RAM/workspace peaks, growth between samples and workload/timing metadata. Once
the updated collector and migration 027 are installed, operators can enable,
disable or change its interval without restarting the service or its models.
Its default is 15 seconds; use five seconds initially to investigate storage
bursts. Baseline operational history below retains its existing configuration.

### Operational history

Enable the optional collector on the next laptop launch with
`--operational-metrics` (default sampling interval: 15 seconds). For a VM, set
`[monitoring] enabled = true` in its TOML; the example includes this section.
Existing TOML files without the section keep collection disabled. The interval
can be 5–300 seconds. Default retention is 30 days, with at most 20,000 hourly
summary rows per pool. Retention and the row cap are configurable on the VM.
Apply the reviewed batch migration 022 before starting the updated service;
status inspection itself never migrates a database.

The same status command reports the last seven days under `metrics` in JSON:

- Host CPU utilisation, hypervisor CPU steal time, RAM use based on Linux
  `MemAvailable`, and the fraction used on the state filesystem. Host readings
  include SingleRun, aggregation, PostgreSQL and other processes.
- Per-attempt MultiRun CPU utilisation relative to its observed CPU allowance,
  total cgroup memory and working memory after subtracting inactive file cache.
  Storage percentages use the verified native project-quota counters. Without
  those counters, per-model storage is unknown; the collector does not repeatedly
  scan large output directories. Per-model details do not include SingleRun.
- Sampled seconds at or above 80% and 90%, sample counts, means and peaks.
  These are estimates from observations, not continuous monitoring. Gaps, resets,
  unavailable readings and intervals crossing a changed resource limit are not
  charged as pressure at that limit. Missing readings are unknown, not zero.
- Request counts, mean/peak time to response headers, mean/peak full-response
  duration and interrupted transfers, grouped by fixed route category and status
  class. Full download duration includes the client's transfer time. No request
  URLs, cookies, query strings, names, owner IDs, emails or bodies are recorded.
- Authenticated supported-bound rejections and accepted form reviews. Counts
  identify the first rejected population, horizon, repetition, configuration,
  resource or unprofiled-workload bound, not individual researchers. YAML imports
  and sweep generation also record classified bound rejections. Invalid syntax
  and unauthorised requests are not counted as legitimate workload demand.
- Initial queue waiting time (submission to first reservation), successful
  completion time including waiting/retries, attempt outcomes and confirmed
  resource-growth/retry events, read from the existing queue records. Preparation
  jobs are excluded from configuration timing averages. Counts are restricted to
  the selected pool and reporting period.

Collection uses a separate background thread and bounded queues/transactions.
HTTP callers only enqueue numeric observations; collection never renews leases,
authenticates sessions, claims jobs or changes resource allowances. Hourly writes
use a separate advisory lock and do not lock the scheduler's pool row. A flush
receipt prevents a lost commit reply from counting the same snapshot twice.
On overload or database outage, bounded buffers can discard measurements; the
report includes discarded/unavailable counts and collector timestamps. A stale
collector is reported explicitly. “Recent samples” is not proof that an
application process is alive. The row cap can shorten the retained period on a
busy service; `last_recorded_at` and sample coverage help interpret each series.

Migration 022 adds operational history only and leaves existing jobs/policies
unchanged. Native backup/restore includes these tables with the other schema
records. Older application code cannot open the newer schema; follow the existing
verified restore procedure when rolling back to code that lacks this migration.

### Visualiser summary-cache reprocessing

The [iterative-experiment candidate](EXPERIMENT_ITERATION.md) retains completed
scenarios' summaries as well as pending-pair dependencies. `summary_retention`
separates their admitted/deferred configuration counts from pending-comparison
counts. Its candidate periods are seven days for summaries and seven days for
finished charts, with an 8-GiB summary allowance inside a 16-GiB total Visualiser
allowance. These settings are independent of simulation CPU/RAM and require
enough actual disk capacity. Routine raw expiry preserves independently retained
aggregates; current permissions and explicit deletion still apply. New backups
include committed summaries and ready charts. See
[retention and recovery](VISUALISER_RETENTION.md) before adopting these settings.

When the updated private summary cache is used, the same read-only status command
includes `summary_cache_usage` in JSON and a short text summary. This is bounded
private filesystem metadata, independent of the optional history collector and
requiring no new database migration. It is absent/unknown for older deployments
or until the cache records activity.

It reports hits/misses, capacity evictions, and genuine raw-parser attempt counts,
elapsed nanoseconds and logical CSV input bytes. `eviction_raw_*` is the subset
whose cache lookup had a retained record of a capacity eviction. A first/unknown
miss, source/calculation change, explicit invalidation, rejected entry or replay
of precomputed summaries is not attributed to eviction. Failed attempts are
separate from completed ones; `unfinished_raw_starts` includes still-running or
hard-interrupted attempts for which no end record was possible.

Counters accumulate from `started_ns` across ordinary service restarts; compare
two snapshots for interval rates. They are not the seven-day DB-history window.
The reason history retains at most 1,024 hashed identities for 30 days; its age
and overflow counts show when eviction attribution becomes incomplete. A lost
marker makes a later miss unknown, so the measured eviction cost is a lower
bound. No names, keys, paths or scientific rows appear in the exported snapshot.
Corrupt/unreadable tracking is reported unavailable rather than zero. Updates
are best effort; an update failure can leave the last valid snapshot, so check
`updated_ns`. Tracking does not prevent scientific processing. The full design and measured saved-result
sizes are in [Visualiser preparation](VISUALISER_PREPARATION.md#measuring-eviction-led-raw-reprocessing).

### Focused proof

After any ongoing scientific calibration has finished, run:

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 ~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --proof-only \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/monitoring_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements.txt" requirements-vm.txt
```

The proof uses disposable PostgreSQL and fictional files. It checks scheduler
isolation, retention/caps, numeric-only reports, streaming observations, migration
compatibility, frozen starting profiles and runtime lifecycle. It launches no
scientific simulations and sends no emails. A pass requires no skipped cases.

**Validated 11 October 2026:** all **154 cases** passed, with no failures, errors
or skips. The [native proof report](/home/rer/simpaths-benchmarks/postgres-queue-20261011-193041/model-proof/report.json)
records the checks; the [driver report](/home/rer/simpaths-benchmarks/postgres-queue-20261011-193041/report.json)
confirms success and temporary database cleanup. Starting-profile compatibility
is verified with fictional workloads; scientific allocation recommendations
still require the remaining calibration and hosted tests.

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
upgrades or creating lock files. The matching backup/restore commands reuse the
verified release inventory and build a complete file/table manifest under
maintenance locks. They do not parse terminal output or treat this bounded status
report as a backup manifest.

## Backup protection status

The separate scheduled runner reports its private journal without user data:

```bash
python -m deploy.multirun.backup_schedule status \
  --config /etc/simpaths-online/backup.toml
```

It shows the latest verified backup ID/checksum, its age, off-machine-copy status,
pending capture/encrypt/copy stage and failure/alert state. Nonzero exit means no
verified backup, overdue protection or failed work. Include it in independent
host monitoring; the VM cannot email if it is down or its timer is disabled.
Status does not capture, decrypt, send mail or delete files. Use
[the recovery runbook](BACKUP.md) for schedules, key/host pins, retention and isolated
restore/activation. No timer, host or alerts are enabled by this command.
