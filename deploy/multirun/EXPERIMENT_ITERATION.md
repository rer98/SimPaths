<!-- (C) Copyright 2026, by Ross Richardson

Iterative experiments, immutable execution history and retained Visualiser data.
@author ross richardson
-->

# Add configurations to an existing experiment

## User workflow

Choose **Add Configurations** in My Jobs or Results. The form starts with one new
card copied from the experiment's last configuration. Edit it, duplicate it or
add more cards, then review and submit. Existing simulations are not rerun.
The original experiment name, baseline, first seed and repetition count are kept.
The form explains that it is adding to an existing experiment and preserves that
mode through a page refresh. **Reset experiment** starts a separate draft.

New configurations appear with the old ones in the original experiment's My Jobs
and Results pages. An addition moves the experiment towards the top of My Jobs.
Users can choose any available scenario as the Visualiser baseline for a later
comparison. Automatic preparation uses the experiment's original baseline.
Comparisons remain confined to this explicitly linked experiment, with current
owner, dataset and output-access checks. Unrelated submissions are not linked
merely because their owner or random seeds match.

## Frozen history and admission

Migration **028** records a flat link from each additional submission to its
original experiment. Every submission retains its own immutable specification,
request hash, resources, retry policy and execution records. Read-only group
views combine their configuration lists. Raw-output validation continues to use
each job's original specification, so adding work does not change an old
container's identity or invalidate an already prepared chart.

Review and confirmation both enforce the original seed plan and model image,
unique configuration identifiers, current cumulative configuration limits,
current input permissions and normal queue/resource limits. Confirmation is
serialized by the existing pool transaction. Duplicate confirmation is
idempotent; two additions using the same configuration ID cannot both succeed.
The new work receives the currently reviewed resource policy. Existing work
keeps its frozen policy. Original inputs must still be available to run new work
that uses them; copying a configuration does not recreate expired inputs.

Earlier raw outputs are protected while linked configurations are unfinished,
using the existing retention and notice rules. This does not grant access after
revocation or reverse a deletion request. Completion notices identify their
submission and explicitly explain that other configurations may still be
running. Their Results link opens the original experiment. Earlier completed
attempts remain unchanged. Downloads in progress retain their original frozen
selection when another configuration is added.

## Retain data for fast later comparisons

The VPS 12 candidate enables both completed-scenario summary retention and
finished-chart retention:

```toml
[visualiser]
retain_completed_summaries = true
summary_hours = 168
cache_hours = 168
summary_cache_mib = 8192
cache_mib = 16384
```

These settings supplement the existing reviewed build, summary-reuse, worker and
publication settings in `vm/throughput.toml.example`; they are not a complete
standalone service configuration. The summary allowance is included in the total
Visualiser allowance. The example reserves 2 GiB of temporary work per active
worker within that same total. These are bounded application disk allowances,
not kernel-enforced per-result filesystem quotas or extra RAM reservations.
Verify the host's usable capacity, raw-output budget, uploads, downloads, Docker,
PostgreSQL and backup space before adopting the example.

Per-seed summaries for **every authorised completed scenario** are protected from
ordinary LRU eviction for the configured period, including after all current
comparisons are ready and when no baseline was selected. Protection is persisted
under the private summary-cache lock and survives an ordinary service restart.
It begins at validated simulation completion and is capped by owner approval,
independently of routine raw-output expiry. Reads and draft creation do not renew it.
Pending-comparison holds remain in addition to these completed-scenario holds;
shared seed entries count once. Existing promises take precedence over new work.
Capacity pressure defers preparation rather than discarding protected summaries
or restarting simulations. Operator status separates completed-scenario holds
from pending-comparison holds and reports deferred storage requests.

Finished chart files are retained for the independently configured `cache_hours`
period, beginning when their publication becomes ready. Reads do not renew it.
The Results page shows the chart expiry. Ready results are not evicted to admit
new ones; byte and entry limits can defer new preparation. No unlimited retention
or instant preparation is promised when those limits are exhausted. The larger
candidate budget is a planning allowance, not a measured production requirement.

Keeping intermediate summaries lets a later configuration reuse old scenarios'
per-repetition statistics. Keeping finished publications and chart indices lets
returning users immediately reopen existing comparisons. A changed calculation
build, expired summary, invalid source or genuinely new statistic may still
require raw processing. Two-seed laptop measurements do not establish hosted or
ten-seed regeneration times.

## Current boundaries and follow-up

### Agreed storage-pressure policy — awaiting implementation

The agreed extension to the current fixed budgets should:

- Make finished publications/chart files eligible for pressure-driven eviction
  after 72 hours without authorised user use, measured from publication when
  never opened. Reclaim least recently used eligible results only as needed.
  Preserve intermediate summaries and protect active reads/downloads/preparation.
- Persist an explicit marker for intentional eviction. Periodic discovery must
  not immediately regenerate an evicted publication. An authorised user request
  can regenerate it; genuinely new completed work remains eligible for automatic
  preparation. Missing summaries or invalid/deleted sources prevent a promise of
  fast regeneration. Update the displayed retention wording before enabling this
  policy; its present fixed expiry alone does not describe pressure eviction.
- Notify administrators at 50%, 70% and 85% occupancy, with an immediate blocked
  preparation incident. Monitor summary and total byte/entry limits as well as
  actual filesystem space. Deduplicate notices, filter short temporary spikes
  and define recovery/re-arm rules.
- Support persistent, validated live increases from an initial allowance to an
  operator-approved maximum. Try normal expiry, then bounded growth, then
  eligible finished-file eviction; otherwise defer preparation. Growth must
  account for space already promised to simulations and other service work,
  reserve processing scratch and leave filesystem headroom. Current physical
  free bytes alone do not establish uncommitted capacity.
- Use a shared total allowance with appropriate summary protection and working
  headroom. There is no required 50:50 summary/publication split. The current
  summary limit is a ceiling inside the total, not a preallocated partition.

None of these new storage-pressure actions is enabled by the repetition-speed
benchmark. See [VISUALISER_REPETITION_TRIALS.md](VISUALISER_REPETITION_TRIALS.md)
for that local benchmark and the separate future storage-policy correctness
checks. Current retention behaviour remains as described above.

### Other boundaries

- Chart access and summary reuse survive **routine raw expiry** using verified
  source provenance, while current permissions and explicit deletion still
  control access. Independent summary/chart periods accept 1–8760 hours;
  defaults remain seven days. New offline/online backups include committed,
  verified summaries and ready publications/indices. See
  [VISUALISER_RETENTION.md](VISUALISER_RETENTION.md) for semantics, recovery and
  evidence. Old backups contain no derived Visualiser payload.
- Disk limits are global application budgets. Measure demand and provide enough
  space; accepting a simulation does not yet reserve its future publication bytes.
  Add forecasted retained-result admission if production measurements require
  a stronger guarantee that every completed job has publication space ready.
- Installed-host UID isolation, mixed simulation/aggregation latency, genuine
  ten-seed sizing and storage-pressure trials remain deployment acceptance work.

## Validation

Focused native tests cover ownership/CSRF, immutable original attempts and ready
charts, idempotence, concurrent ID collisions, cumulative limits, seed-plan
rejection and comparisons with completed additions. A separate native Chromium
proof checks the real form and refresh through the private service. Local tests
exercise completed-summary holds under pressure/restart and production SimPaths
form translation, including large integer seeds.

On 17 October, all **84 native database checks** passed without skips in
[/tmp-codex/experiment-extensions-retry-20261017-122328/report.json](/tmp-codex/experiment-extensions-retry-20261017-122328/report.json).
The **three native Chromium/private-RPC stages** passed with no browser or private
processor errors and cleanup confirmed in
[/tmp-codex/experiment-extensions-browser-20261017-122701/model-proof/report.json](/tmp-codex/experiment-extensions-browser-20261017-122701/model-proof/report.json).
The local run passed **76 checks**; an additional native operator-status case was
skipped locally because it requires PostgreSQL. Two of the passing checks use
the actual pinned calculation build with tiny fictional input files.

The focused final PostgreSQL run passed **45 checks** without skips: nine
experiment-addition cases and 36 queue/migration cases. It also verifies that
downloads remain valid during an addition and completion notices identify the
submission they cover while other linked work is queued. Cleanup was confirmed
in
[/tmp-codex/experiment-extensions-final-20261017-123500/report.json](/tmp-codex/experiment-extensions-final-20261017-123500/report.json).
The earlier 84-case report predates these final regression additions; the two
runs overlap and are not separate counts of unique tests.
