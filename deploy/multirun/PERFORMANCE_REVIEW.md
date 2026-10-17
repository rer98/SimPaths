<!-- (C) Copyright 2026, by Ross Richardson

Live operator controls, sampled resource evidence and future allocation review.
@author ross richardson
-->

# Performance review without interrupting jobs

## Current decisions

- Keep the original working-storage allowance as the basis of the ceiling.
  During testing, allow up to **2 times that allowance**, or 100% extra.
- Keep **25% growth steps** initially. Each step is calculated from the current
  allowance; the ceiling remains fixed relative to the original allowance.
- Permit finite decimal multipliers such as `1.3`, in the existing range 1–4.
  TOML uses `storage_multiplier = 1.3`; the local flag is
  `--recovery-storage-multiplier 1.3`. The default remains 2.
- Retain administrator growth notices and the durable recovery-event history.
- Measure absolute resource demand on the VPS before reducing starting allowances
  or growth steps. Review data informs operator-approved profiles; it does not
  change accepted jobs or automatically tune the scheduler.

Decimal caps use exact decimal multiplication and round **down** to whole MiB,
so rounding cannot exceed the requested ceiling. Fractional policies carry
`jasmine.resource-recovery.v2`; existing integer policies retain version 1 and
their exact frozen limits. An integral decimal such as `2.0` normalises to 2.
Changing the operator's defaults affects newly reviewed work, not queued/running
submissions. Broker and pool ceilings still apply.

## Why measure before trying 10% steps?

At the current 85% trigger, an 8 GiB allowance has approximately 1.2 GiB remaining
at exactly that threshold, whichever growth increment is selected. A 10% step
would add 0.8 GiB to the first allowance; a 25% step adds 2 GiB. Smaller steps
need less additional capacity and may let more configurations continue when disk
is scarce. They also leave less space after an increase and can require more
updates during a sustained burst. Polling and quota-update latency matter as well
as the increment. No polling threshold guarantees that an arbitrary burst fits.

Our retained-output totals do not establish the largest temporary file or the
largest short-lived burst. Input copies, native snapshots and database writes
need working space too; existing preflight estimates include initial copies.
Use a **five-second review interval** for the initial burst investigation. It
measures positive net workspace growth between readings, not every file write or
largest individual file. A file created and deleted between readings can be
missed. Do not infer a probability of quota failure from these preliminary data.

**To do after representative VPS measurements:** compare 10% and 25% increments
under real output workloads, including smaller starting allowances, repetitions,
input staging and busy disk conditions. Assess whether a minimum increment or a
write-rate-based safety margin is warranted. Changing to 10% is supported by the
existing setting, but is not enabled by this work.

## Install once, then switch the review live

The updated service includes a separate private review collector even when
baseline operational monitoring is disabled. It begins with review collection
disabled. Install this application update and apply reviewed batch migration
**027** (after 026) using the normal controlled deployment procedure. Introducing
new code/schema still requires that deployment step; subsequent review switches
do **not** require an application restart, cancellation or model relaunch.

Run commands as the private service account, using its installed environment:

```bash
# Start a new window at five-second intervals for storage-burst investigation.
python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml \
  enable --sample-seconds 5

# Inspect requested controls, collector acknowledgement and recorded attempts.
python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml status

# Export bounded, labelled records for analysis.
python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml status --json --limit 1000

# Stop new sampling while retaining the records already collected.
python -m deploy.multirun.performance_review \
  --config /etc/simpaths-online/multirun.toml disable
```

For an existing laptop service, replace `--config ...` with
`--frontend /path/to/JAS-mine-web --state /path/to/private/multirun-state`.
`status` can additionally use `--dsn-file`, `--pool` and `--schema` for an existing
local database. Enable/disable commands do not contact Docker, PostgreSQL or SMTP
and never create a missing service-state directory.

The default interval is **15 seconds**, configurable from 5 to 300 seconds. A
private control-file poll runs every five seconds. Enable, disable and interval
changes are acknowledged on that poll; native calls already in progress can
delay acknowledgement. Repeating `enable` with different settings starts a new
observation window, so readings cannot bridge a disabled interval or silently
mix intervals. `status` distinguishes requested controls from their recorded
acknowledgement. An acknowledgement is not a process-health guarantee.

Controls survive ordinary restarts. Re-enabling starts a fresh window; restarting
the collector resets interval baselines rather than charging unobserved time.
Turning review off leaves safety checks, live resource growth, normal monitoring,
the website and simulation dispatch running. Pending captured readings can still
flush after disable. Normal application shutdown joins the review thread before
releasing the service's lifecycle fences.

## What the records contain

Each row represents an **attempt observed during one review window**, rather than
a complete scientific benchmark. The JSON report labels the following fields:

| Field/group | Meaning |
| --- | --- |
| `session_id`, `attempt_id`, `job_id` | Opaque observation-window and work references |
| `kind` | MultiRun simulation or input preparation; interactive SingleRun is not included |
| `sample_seconds`, `samples`, first/last sample times | Configured interval and actual coverage |
| `peak_memory_bytes` | Largest sampled total cgroup memory, including accounted file cache |
| `peak_working_memory_bytes` | Largest sampled memory after subtracting inactive file cache; includes Java and native memory |
| `peak_storage_bytes`, `storage_samples` | Largest sampled allocated project-quota bytes and number of known storage readings |
| `peak_net_growth_bytes` | Largest positive change between consecutive valid workspace readings |
| `peak_net_growth_bytes_per_second` | Largest measured net-growth rate; not total write traffic or largest-file size |
| Min/max CPU, RAM and disk limits | Observed allowances, including live changes |
| Min/max concurrent model containers | Observed contention context within the MultiRun pool |
| `cpu_ns`, `observed_seconds` | CPU time observed between valid readings and corresponding sampled coverage; gaps are excluded |
| Start/finish, outcome and `elapsed_seconds` | Attempt timing from original queue records; elapsed includes setup, host contention and validation |
| `first_observed_after_start_seconds` | How late observation began; enabling partway through a run cannot recover earlier peaks |
| `workload` | Initial population, requested years/repetitions, allowlisted output collectors, resource profile and model identities where present |
| `recovery_events` in each attempt | Counts of memory/storage/heap growth, waiting and retries over that attempt's retained history |

Initial population is **not** the largest population reached during the model.
Workload counts do not expose seeds, policy/input values, labels, file paths,
person records, credentials or owner email addresses. Approved private operators
can export these metadata; this is not a public browser endpoint.
The report's top-level `recovery_events` totals cover the pool's retained recovery
history. Neither these totals nor the per-attempt counts are restricted to the
review window; use ordinary operational history for counts in a selected period.

Raw counters come from the verified Docker container and XFS quota broker; the
collector does not walk or parse large output files. Without verified quota
accounting, storage is **unknown**, not zero. Sampled peaks can miss temporary
spikes and end-of-run files. Failed or partial attempts must not be treated as
proof of requirements for successful complete configurations. Compare the same
model/input/output contract and use several successful runs plus safety headroom
before approving a smaller production profile.

The ordinary [operational history](OPERATOR.md#operational-history) remains useful
for host CPU steal, shared-server load and browser latency during each window.
VPS observations describe actual service performance; they cannot reproduce the
controlled conditions of a quiet-laptop benchmark.

## Bounds, failures and retention

At most 128 active attempts are sampled by one pass. Collection buffers are
bounded; missing/dropped readings and exception **types** appear in private
collector status. Model/native/database failures affect the review only. Storage
growth monitoring remains a separate safety mechanism.

The default metadata retention target is **90 days**, with **10,000 attempt-window
rows**. `enable --retention-days N --max-rows N` accepts 1–365 days and 100–100,000
rows. Successful review writes apply expiry and row caps within the selected
pool. When review is disabled, records remain available; expiry pruning resumes
with successful collection writes. `status --limit` is an export bound (maximum
2,000), with an explicit truncation flag. It does not expand retention.

Writes use their own advisory lock and short timeouts, without acquiring the
scheduler's pool-row lock. Flush receipts prevent double counting after a lost
commit acknowledgement. Native backup/restore includes both review tables through
the normal schema snapshot. Review controls are private local configuration;
check/reapply them explicitly on a restored host before collecting again.

## Administrator alerts

Confirmed storage increases already produce `resource_admin` notifications and
durable resource events. Real delivery requires the normal notification switch,
administrator recipient and SMTP configuration. These alerts operate independently
of review mode. The existing status history counts growth/wait/retry events for
the chosen reporting period, and review JSON adds per-attempt counts. Frequent
growth is a reason to inspect workload differences and revise tested profiles;
it is not proof that all jobs need a larger starting allowance.

## Validation

The local tests cover decimal ceilings, unchanged integer policies, exact MiB
rounding, private controls, dynamic intervals, unknown storage, observation gaps,
changed resource limits, bounded commit retries and safe workload metadata.
The disposable native proof uses fictional samples and captured email senders:
`monitoring_proof.py` includes hot controls, database aggregation, scheduler
isolation, runtime lifecycle and native backup/restore checks. It does not certify
sampling overhead or burst safety on the chosen VPS; those remain host tests.

On 17 October, all **179 native proof checks** passed with no failures, errors or
skips and cleanup confirmed in
[/tmp-codex/performance-review-native-20261017-105635/report.json](/tmp-codex/performance-review-native-20261017-105635/report.json).
The final focused local run passed **35 checks**. After adding the configured
sampling interval to each stored window, the focused **seven-case PostgreSQL
rerun passed**, with cleanup confirmed in
[/tmp-codex/performance-review-final-20261017-113932/report.json](/tmp-codex/performance-review-final-20261017-113932/report.json).
The 179-case report predates that metadata addition; the focused rerun verifies
the final stored-window format and live controls.
