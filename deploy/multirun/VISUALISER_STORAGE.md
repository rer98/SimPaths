<!-- (C) Copyright 2026, by Ross Richardson

Visualiser disk pressure, live storage approval, user access and recovery.
@author ross richardson
-->

# Visualiser storage management

## Policy

`visualiser.storage_pressure = true` enables the policy in the private executor.
Legacy configurations default to `false`. It needs bounded helpers and reusable
summaries. The throughput candidate enables it with an initial and maximum total
allowance of **16 GiB**; additional total storage needs explicit operator approval.

Before admitting preparation, the policy applies normal expiry, checks the
shared budget and physical capacity, attempts approved growth, then reclaims
eligible finished publications/chart files only as needed. Otherwise preparation
waits and retries; simulations are not rerun. Entry limits can also defer new
preparation even when bytes are available.

- A finished publication becomes eligible after **72 hours without authorised
  user use**, starting at its ready time if never opened. The oldest eligible
  results lead eviction. An explicit ready-result request/status read, chart
  read or comparison download records use; automatic discovery and the Results
  inventory do not. Access does not extend the independent chart expiry.
- Active readers, portable downloads and preparation hold shared/exclusive job
  locks. Eviction never waits for or cancels those operations. Helper children
  inherit the existing read/preparation locks.
- Only finished publications, chart indices and abandoned private working files
  are removed by pressure eviction. The shared intermediate summary cache and
  its authorised retention/dependency holds are preserved.
- A durable **evicted** status is committed before removing files. It survives
  restart and the original chart deadline. Background discovery leaves the
  exact evicted selection alone. A new completed scenario has a different
  immutable selection and remains eligible for automatic preparation.
- An authorised **Prepare Visualiser** request can requeue an evicted result.
  Permission checks still apply, including revocation and explicit deletion.
  Routine raw expiry remains independent. Regeneration from retained summaries
  is possible only while those exact summaries remain available; the policy
  does not promise regeneration from missing data.

Eviction records have their own bounded metadata allowance, equal to the
configured publication entry limit. They do not consume publication entry slots.
If that marker allowance fills, further pressure eviction defers rather than
forgetting markers and causing repeated deletion/regeneration. Revocation or
explicit regeneration can retire a marker. Administrators can see entry/marker
pressure separately.

## Shared allowance and physical headroom

There is no 50:50 partition. `summary_cache_mib` is the initial summary ceiling
**inside** `cache_mib`; `summary_cache_max_mib` is its independently approved
ceiling. The policy can increase it within the shared total when summary demand
needs it. All configured workers' scratch and bounded publication writes retain
headroom. Missing summaries promised to admitted comparisons count before they
are written. This is separate from simulation CPU/RAM handover.

Total allowance growth uses 25% steps, or a larger directly required amount,
within `cache_max_mib` or a subsequently approved live ceiling. Approval alone
does not allocate disk or trigger immediate growth. The admitted limits persist
in private `.storage-budget.json` and survive restart.

Ordinary preparation checks the quota broker's remaining workspace promises,
its filesystem reserve, extra configured service headroom and helper working
space. A **total allowance increase** additionally leaves room for the entire
configured model pool, protecting against new model admissions immediately after
the measurement. This is deliberately conservative: apparently free bytes need
not permit an increase. Without a workspace broker, the full pool is used for
the ordinary check too. An unavailable capacity check cannot approve growth.

`filesystem_headroom_mib` defaults to **16384 MiB** for database, uploads,
downloads and other service work, beyond model promises. Review this on the
actual host. These are application allowances and admission snapshots, not
physical preallocation or kernel quotas on the whole Visualiser tree. External
host writes can still exhaust a filesystem. Current model quotas remain enforced
by their existing broker. New simulation admission still does not forecast its
future Visualiser publication size.

## Administrator warnings

The existing private email outbox records administrator incidents at **50%, 70%
and 85%** for total bytes, summary bytes/promised bytes, publication entries,
summary entries/promised slots, eviction markers and filesystem headroom pressure.
Routine measurements are at most once every **30 seconds**. A threshold must
persist for **five minutes** before it opens an incident, filtering temporary
working spikes. A level resolves/re-arms after occupancy falls five percentage
points below it. Resolved incidents can warn again on a later sustained crossing.

Blocked preparation opens an immediate incident, held across short retry gaps to
avoid repeated mail. Summary dependency backpressure is included. Notices are
deduplicated by metric and threshold in the existing database outbox. They go to
the configured administrator, contain no user identities or scientific data, and
are sent only when existing notification delivery is enabled. With delivery off,
incidents and private numeric snapshots are still recorded.

The private operator status JSON includes `visualiser_storage`: current/approved
limits, numeric metrics and active warning levels. Its reads do not change
retention, access times or the budget.

## Live approval

Once the service has initialised the policy, inspect its private controls:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.visualiser_storage \
  --state /PATH/TO/EXISTING/PRIVATE/MULTIRUN/STATE
```

After reviewing host capacity, an operator can raise ceilings without restarting
the service or stopping simulations, for example:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
~/simpaths-browser-tests/venv/bin/python -m deploy.multirun.visualiser_storage \
  --state /PATH/TO/EXISTING/PRIVATE/MULTIRUN/STATE \
  --maximum-mib 32768 --summary-maximum-mib 24576
```

For an installed VM use its Python environment and `--config /path/to/private.toml`
instead of `--state`. The command only changes approval; physical checks still
control admission. It rejects reductions, unsafe paths, inconsistent ceilings,
totals above 128 GiB and summary ceilings above 64 GiB. Decreasing admitted
allowances requires a separate drained/reviewed change. No public HTTP route
exposes these controls.

## Backup and compatibility

New retained-Visualiser backup inventories use version **v2**, including ready
publications, verified summaries, user-access receipts, eviction records and
the admitted/approved budget. Partial live calculations remain omitted. Restoring
an eviction record does not recreate its payload automatically. Physical capacity
is checked on the replacement host before further managed preparation/growth.
Existing v1 retained backups remain readable; they have no saved storage control
or pressure eviction records. Scientific publication and chart formats are
unchanged, and no database migration or model-image rebuild is required.

Keep storage-aware private executor code when restoring or rolling back a service
which has eviction records; older automatic-preparation code does not understand
those records. The normal private ownership and permission checks remain required
on restored files.

## Verification

The local correctness tests use tiny fictional files, controlled clocks and
captured senders. They cover idle/access selection, active locks, marker-first
cleanup, restart/discovery suppression, on-demand requeue, safe approval,
competing growth requests, model/storage promises, summary budgeting and warning
hysteresis. Adapter/backup tests exercise the real summary cache and verified
retained-file capture. The browser progress test checks the conditional retention
wording and the evicted status without automatically submitting work.

Native correctness verification passed on **18 October 2026**:

- **90 queue/HTTP checks** cover storage policy, automatic preparation,
  independent retention, Results access, notifications and resource handover.
  Evidence: `/tmp-codex/visualiser-storage-native-20261018-213224/tests.log`.
- The two focused storage HTTP checks also passed after the final reader-lock
  changes. Evidence:
  `/tmp-codex/visualiser-storage-native-retry-20261018-214309/tests.log`.
- **54 recovery/configuration/runtime checks passed, with no failures, errors
  or skips**. Offline and online restoration preserve summaries, charts, source
  authority, eviction suppression and approved limits. Disposable PostgreSQL
  cleanup was confirmed. Evidence:
  `/tmp-codex/visualiser-storage-native-final-20261018-215233/model-proof/report.json`
  and its parent `report.json`.

Earlier recovery fixtures were corrected to reserve realistic scratch and create
private output directories matching the real container. The admission and
backup permission guards remain enforced; earlier failed reports are preserved.

Local checks include 19 storage-policy tests, five adapter/operator-command tests,
25 backup-file tests and 14 browser progress tests. The native recovery proof
checks PostgreSQL and retained private files through offline/online backup and
restore; encryption/transport verification remains in its existing separate
suite. The complete command below runs no scientific simulations or real emails:

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --test-pattern test_visualiser_storage.py test_visualiser.py \
                 test_visualiser_automatic.py test_visualiser_independent_retention.py \
                 test_results.py test_storage_notifications.py test_processing_handover.py \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/visualiser_storage_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements-backup-proof.txt" \
  --output "/tmp-codex/visualiser-storage-native-$(date +%Y%m%d-%H%M%S)"
```

Full-size sizing, actual quota-broker/mixed-load disk behaviour and website
responsiveness still require hosted measurements. This correctness proof is
independent of the quiet scientific resource benchmarks.
