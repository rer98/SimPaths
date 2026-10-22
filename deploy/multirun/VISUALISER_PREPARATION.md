<!-- (C) Copyright 2026, by Ross Richardson

Agreed automatic Visualiser preparation design and resource-validation plan.
@author ross richardson
-->

# Automatic Visualiser preparation and resource tests

For the focused two/five/ten-repetition benchmark of regeneration from already
retained summaries, see [VISUALISER_REPETITION_TRIALS.md](VISUALISER_REPETITION_TRIALS.md).
The separate [raw-to-intermediate resource matrix](VISUALISER_SUMMARY_TRIALS.md)
uses only the two genuine repetitions per configuration, testing 2/2.5/3 GiB,
one/two vCPUs per worker and one/two workers. It measures source verification
and raw parsing separately, without final comparison or chart generation.
The [preparation scaling benchmark](VISUALISER_SCALING_TRIALS.md) adds parallel
repetitions within one configuration under the same total 5 GiB/2-vCPU budget,
and one/two/four workers across two configurations through finished chart reads.
Pressure eviction, warnings and bounded live expansion are implemented behind
`visualiser.storage_pressure`; see [VISUALISER_STORAGE.md](VISUALISER_STORAGE.md)
for policy, approval commands and verification status. The throughput candidate
enables the policy; running a benchmark does not change an installed service.

**Decision record: 15 October 2026; follow-up decisions and resource evidence updated 18 October 2026. Opt-in implementation; verification evidence and outstanding capacity/host acceptance are recorded below.**

Completion-triggered preparation, protected capacity and bounded summary reuse
are implemented behind explicit operator settings. Existing deployments retain
their fixed allocations and on-demand preparation until enabled. Bounded retention
for pending comparisons is implemented independently of worker resources. Direct
[simulation-to-preparation handover](VISUALISER_HANDOVER.md) is now integrated
behind its own operator setting, with native database acceptance passed. The checks
distinguish local and native database verification from outstanding hosted
resource and deployment acceptance.

## Current behaviour and evidence

- Results-page **Prepare Visualiser** requests still work. With automatic mode,
  the private dispatcher consumes durable completion events before admitting
  replacement simulations. It queues standalone results and each ready selected
  baseline/alternative pair without waiting for the whole experiment or the next
  periodic scan. The bounded 60-second scan remains a recovery mechanism.
- Preparation is sequential by default. The opt-in production
  [parallel seed integration](VISUALISER_PARALLEL.md) runs two independent
  repetitions within one admitted 5-GiB/2-vCPU group, falling back to one worker
  when only its ordinary allowance fits. Individual JavaScript calculations
  remain unchanged; extra CPU alone does not make one calculation parallel.
- The server prepares aggregates and selected-chart sections. Authenticated
  catalogue/section requests and bounded browser caching avoid repeatedly
  downloading the complete publication.
- A new verified runner supports private role-independent per-seed summaries.
  Opt-in reuse checks source descriptors/hashes, owner and immutable execution
  identities. The bounded cache uses atomic publication and striped process
  locks. Publication expiry retires unheld referenced summaries; pending-pair
  holds preserve those still needed. Source invalidation overrides these holds.
  A bounded process-local hash cache avoids rereading an unchanged baseline for
  every alternative. It binds device/inode, size, nanosecond modification/change
  times and expected hash; replacement or mutation requires a new hash. A service
  restart starts with no remembered source checks. Summary bytes are still
  checksum-verified on every reuse, and authority is independently rechecked.
- The native private executor can protect idle primary-worker capacity and
  enforce helper CPU/RAM/process limits in delegated cgroup v2 groups. A second
  worker can borrow available capacity after verified quota reductions. The
  example starts with one worker pending disk-contention measurements.

The earlier successful 100,000-person, 2019–2070 comparison proof used one baseline and
one alternative with **one matched seed each**. Its processor reservation was
1 vCPU and 3 GiB, with a separately configured 2 GiB Node allowance. That is a
working starting point, not a measured minimum or optimum. The proof did not
measure peak memory of the entire processing group. Its total duration also
included registration, validation and browser checks, so it is not a pure
aggregation timing.

The 17 October raw-output resource comparison also passed with one baseline,
one genuine policy alternative and two matching repetitions each. All 24
resource/cache batches matched their calculation/publication identities, with
no OOMs or cleanup failures. The detailed results below support 3 GiB as a
starting worker allowance and show a throughput/first-result trade-off for two
workers. They do not establish ten-repetition limits or hosted mixed-load
responsiveness.

The focused raw-to-intermediate matrix completed on 18 October with **34/36
passing batches**. Two 3-GiB batches failed in resource sampling, rather than a
reported scientific calculation, and remain failed in the original evidence.
All 2/2.5-GiB batches passed. The [recorded results and focused follow-up](VISUALISER_SUMMARY_TRIALS.md#native-results--18-october-2026)
show a throughput benefit from two workers and modest gains from extra CPU;
the 2.5-GiB profiles offer a useful memory/time tradeoff. Sampler diagnostics
are improved, but the exact original trigger remains unrecorded and unresolved.
Production settings remain unchanged.

The [18 October native scaling run](VISUALISER_SCALING_TRIALS.md#native-results--18-october-2026)
passed **18/18 cases** with matching data, no OOM/sampler failures and confirmed
cleanup. Two seed workers within the same total 5 GiB / 2-vCPU group made one
configuration's charts ready in 5.74 minutes versus 7.84 serially. Across two
configurations, four workers within 10 GiB / 4 vCPUs completed all required charts
in 7.80 minutes versus 11.83 with two workers in 5 GiB / 2 vCPUs. First standalone
charts arrived later with four workers (7.43 versus 5.82 minutes), and completion
rates per allocated vCPU decreased. This supports measured parallel preparation
for faster result readiness. Seed parallelism and direct capacity handover are
now integrated behind operator settings; native handover acceptance passed and
hosted mixed-load validation remains outstanding.

## Agreed preparation sequence

1. After **one configuration completes all its repetitions**, verify successful
   completion, output identity and availability, then queue its reusable
   per-seed summaries and standalone chart aggregates before the next model
   admission decision. The completion transaction records a durable job-change
   event; delivery does not wait for the periodic scan. Eligibility for processing
   is not a promise of immediate worker capacity.
2. If the experiment has a selected baseline, prepare its comparison with
   **every alternative** as soon as both configurations' summaries are ready.
   An alternative that finishes first can prepare its summaries immediately;
   only its paired impacts must await the baseline.
3. Without a selected baseline, prepare configuration summaries and standalone
   aggregates, but defer paired impacts until the user chooses a baseline.
4. Do not calculate alternative-versus-alternative comparisons speculatively.
   Once a baseline is selected, all its alternatives should be available
   together in the Visualiser as their preparation completes.

### Finish all available preparation before waiting — 17 October 2026

**Agreed:** intermediate per-seed summaries are not the stopping point for a
completed configuration whose comparison partner is still running. Combine its
repetitions into standalone estimates and uncertainty intervals where applicable,
then publish its standalone chart sections and metadata. This does not require
another configuration. Automatic mode already queues this standalone pipeline.
Baseline differences and their seed-paired uncertainty must wait until both
configurations have the required verified results and matching seed summaries.

Where comparison inputs are ready, continue through combination and final
publication in the admitted worker when possible. Do not introduce a mandatory
second queue or handover to the protected worker just because raw summarisation
has finished. If an input is missing, finish the available standalone work and
release processing capacity; do not keep a worker or RAM reservation idle while
waiting. One ready baseline/alternative pair remains usable while another
alternative is still simulating or preparing.

**Resource-handover extension, implemented behind an opt-in setting:** prefer assigning safely
released simulation capacity to eligible preparation before a replacement model,
subject to the existing fairness/progress policy. Verify simulation exit and
reservation ownership before reassignment, including restart/failure recovery.
Allocate the worker's measured requirements rather than automatically retaining
the model's whole 5 GiB. Reconsider CPU against current demand; the model's last
1-to-2-vCPU allowance is not an entitlement to an additional unaccounted worker.
Keep protected SingleRun, essential services and emergency memory outside this
handover. Optional-worker borrowing and direct per-configuration transfer remain
distinct mechanisms. The [handover guide](VISUALISER_HANDOVER.md) describes atomic
completion, pending/adopted reservations and continuation. Native acceptance
passed 123 suite and 21 runtime checks; [seed-parallel execution](VISUALISER_PARALLEL.md)
is verified separately.

### Refinement: readiness-driven continuation — 18 October 2026

The following are agreed targets. Direct reservation handover, readiness-led
continuation and selected-baseline admission preference are now implemented.
The baseline-first change passed 28 local checks and 89 native queue, adaptive
CPU, storage, extension and handover checks without skips; evidence is linked in
the [handover guide](VISUALISER_HANDOVER.md#baseline-first-model-admission).
Generalising the independent worker count remains separate work:

- Prefer admitting the selected baseline first within an experiment, subject
  to owner fairness, experiment age, retry backoff and feasibility. Normal
  admission and adaptive CPU planning share this preference. The reviewed form
  order remains unchanged; a waiting baseline does not block runnable
  alternatives. It can still finish last, so simulation order does not establish
  summary readiness.
- Check dependencies whenever a configuration's **intermediate summaries are
  ready**. A simulation-completion event alone cannot establish that a pair is
  ready: configurations can take different times to summarise.
- Before releasing a completed simulation's capacity for another model, use
  that capacity for as much eligible experiment preparation as possible:
  canonical per-seed summaries, standalone aggregates and indexed charts, then
  each baseline/alternative comparison whose two summaries are ready. A pair
  need not wait for either standalone publication to finish, or for unrelated
  alternatives. Give newly eligible comparisons priority over further raw work
  when a worker becomes free; do not interrupt existing calculations or models.
- If the baseline's summaries finish last, continue every ready pair against
  the already summarised alternatives. If an alternative's summaries finish
  last, continue its pair and any remaining eligible experiment work. The
  scenario which finishes preparation last therefore carries the remaining
  work; there is no mandatory handoff to another queue. Recheck dependency and
  authority state before releasing the final reservation.
- Release capacity when all **currently possible** work is done. Do not hold
  it idle for a partner still simulating or summarising. Ready pairs remain
  viewable while other alternatives are unfinished.
- The candidate single-task allowance is 2.5 GiB with elastic 1–2 vCPUs. Retain
  up to the completed model's full 5 GiB when measurements justify two seed
  tasks or a publication backlog in parallel. Account for the coordinator and
  every child inside the total group, not just Node heaps.
- Ready preparation precedes launching queued models. Running models continue
  with at least 1 vCPU; reclaim only their temporary boosts when needed. Once
  higher-priority preparation is admitted, reclaim boosts rather than leave
  enough model RAM idle solely for lack of an additional vCPU.
- Do not make two heavy workers a permanent server-wide ceiling. Admit a
  measured number according to CPU, RAM, scratch and disk contention. First
  compare one, two and four workers on the laptop; then validate concurrent
  models and website responsiveness on the intended storage and VPS.

The benchmark measures the summary-completion dependency graph and parallel seed
tasks with maintained calculation/publication helpers; it does not itself change
live reservations. Production handover now implements the resource transfer and
eligible continuation described in [VISUALISER_HANDOVER.md](VISUALISER_HANDOVER.md).
The existing 60-second admission preference remains for unfunded work. Legacy
defaults remain sequential with handover disabled; the candidate enables two
independent preparations with up to two seed tasks each and direct transfers,
subject to actual shared admission.

Idle protected aggregation CPU may eventually provide revocable model boosts,
provided service demand and ready preparation can reclaim it promptly. Essential
OS/PostgreSQL/web capacity is retained first. CPU quota can be reduced quickly;
committed JVM memory is not an equivalent immediately reclaimable loan. Keep
that distinction when deciding whether to lend unused RAM.

The revised VPS-12 candidate pool is 39.5 GiB and 11 vCPUs, with a 2.5-GiB
protected primary group. Outside that pool, a host with **48 decimal GB** has
about **5.2 GiB** for OS/PostgreSQL/web processes; a host exposing 48 GiB instead
has 8.5 GiB. Verify actual `/proc/meminfo` on the VM. The 0.5-GiB reduction in
both primary and total pool allowances leaves ordinary model capacity unchanged
and gives that headroom to general services. Existing pools require a drained
migration or a new identity before their immutable limits change.

Retain verified per-seed summaries for authorised pending comparisons: finished
standalone averages and confidence intervals cannot replace the original
matching-seed statistics. Bounded dependency-aware holds are now implemented as
described below. Waiting on another configuration does not keep a live RAM
reservation or imply indefinite disk retention.

### Completion delivery and recovery

Migration `025_visualiser_completion.sql` adds an independent Visualiser version
receipt to the existing transactional completion events. The dispatcher drains
bounded batches of changed experiments before model admission, queues permitted
ready configurations/pairs idempotently, then acknowledges the observed version.
If the baseline finishes last, all its already completed alternatives are included.
No aggregation calculations run on the dispatcher thread.

A crash after enqueueing but before acknowledgement safely replays the same keys.
Newer job changes cannot be acknowledged using an older snapshot. Pending event
batches postpone model admission until caught up; surviving simulation leases
continue to receive heartbeats. Owner/source permissions and cache allowances
still apply. Denied or unavailable outputs do not gain background authority.
Email-notification receipts and delivery remain independent.

The periodic scan separately recovers older completions, expired cache entries
and work skipped while sources or cache capacity were unavailable. Its interval
is distinct from the 60-second scheduling preference: neither interval limits
the execution time of a running aggregation worker. The optional second worker
and emergency-memory loan option retain their existing disabled defaults.

Keep sufficient per-seed data for the existing matching-seed differences and
uncertainty calculations. Overall averages alone are insufficient. Preserve the
scientific calculation and suppression rules; this is a scheduling and reuse
change, not a statistical-method change. Show simulation completion separately
from chart readiness, including queued, preparing and failed preparation.

## Reusable private summaries

Design summaries independently of a configuration's current baseline/alternative
role, so changing the baseline can reuse validated work. The private server cache
must bind entries to the completed configuration, original seed identities,
source hashes, immutable model/results format and aggregation implementation
version. Comparison entries additionally bind the selected configuration/seed
mapping and calculation options.

Reuse must retain owner/session authorisation and comparison permission checks.
Source removal, changed source identity, revoked access and incompatible
calculation versions must invalidate use as appropriate. Publication must remain
atomic and private, with concurrent requests deduplicated and failed work safe
to retry. Do not publish raw microdata, private paths or diagnostic contents to
the browser.

Bound cache bytes and entry counts; record storage separately from retained raw
output. Align retention with expected use and source-result retention so an
overnight preparation is not routinely expired before the user returns. Restart
recovery must avoid duplicate workers and partial publications.

### Pending-comparison retention — implemented 17 October 2026

This is independent of simulation-to-preparation CPU/RAM handover. With private
summary reuse enabled, the platform derives dependencies
from authorised configuration snapshots, the selected experiment baseline and
explicitly requested pending pairs. Once at least one member has completed, it
attempts to admit a persistent disk-retention plan for both configurations. It
does not create holds for hypothetical comparisons without a selected baseline,
or for an entirely unfinished pair. Automatic mode registers dependencies on
completion; manually started preparation reconciles them before processing too.
Changing to on-demand mode does not strand existing holds or block a manual pair
when its formerly unfinished partner completes.

- Admitted per-seed summaries are protected from ordinary LRU eviction. Shared
  baseline seeds count once across the owner's pending comparisons. Exact job,
  successful attempt, owner, configuration, seed and calculation/runtime scope
  select the retained entries; existing source hashes and permissions still
  control whether they can be reused. An old attempt cannot satisfy a pending
  partner's future reservation.
- Reserve missing seed summaries at the supported maximum: 64 MiB of metrics
  plus up to 64 KiB of receipt metadata each. Published seeds use their measured
  sizes. This protects capacity for an unfinished baseline/alternative, rather
  than filling the whole cache with results whose partners cannot then fit.
  All promises share the existing summary-cache byte/entry limits. The owner's
  retained-configuration limit follows its configured preparation-entry allowance,
  bounded by the global summary-entry limit. Multiple seeds do not count as
  separate configurations. No larger runtime storage budget is enabled.
- If another dependency cannot fit, leave its preparation queued with a storage
  message and a bounded 30-second retry delay. Check admission before source
  hashing or CSV parsing. Do not evict admitted dependencies, grow the cache
  beyond its cap, repeatedly parse an unadmitted comparison, or restart models.
  Deferred work does not hold simulation-admission CPU priority during backoff.
- Holds have fixed deadlines: the independently configured summary period
  (seven days by default) from the first completed member, capped by owner approval.
  Routine raw expiry does not shorten this period. Reads and repeated
  reconciliation do not renew an existing hold. Source deletion, access loss,
  cancellation/expiry of a required partner or an incompatible dependency removes
  the promise. These checks do not extend raw-output retention or authorisation.
- A ready pair releases its dependency; an unfinished alternative may still keep
  the shared baseline held. Expiring a standalone publication preserves summaries
  held for another pending comparison. Explicit invalidation overrides all holds.
  Retiring a pair with one deleted/denied source invalidates that source; an
  authorised baseline needed by a different pair remains protected.
  Once released or expired, surviving summaries return to ordinary LRU treatment;
  the finished publication remains separate from its intermediate summaries.
- Plans live in the private, atomically replaced `.summary-holds.json`. Metadata
  is capped at 512 KiB/256 comparison records, with at most one bounded temporary
  replacement. New work cannot consume already promised storage if metadata or
  capacity is unavailable. Locks, cache publication and reservation checks are
  shared across processes. Restarts retain promises; bounded rotating maintenance
  rechecks current dependencies every 30 seconds, up to 64 experiments per pass.
  A full metadata allowance delays preparation without stopping model dispatch.
- Read-only operator status reports admitted/deferred comparison counts and shared
  seed-slot counts without private owner IDs, paths, cache keys or scientific
  values. Existing eviction-led raw-reprocessing counters remain available.

The guarantee applies to **admitted dependencies before their fixed deadlines**,
not unlimited experiments, lost/corrupt storage or revoked results. Releasing a
hold permits later eviction; selecting a different baseline after release can
still require raw reprocessing. Already queued preparation reconciles dependencies
before starting, so it does not depend on replaying an old completion event.

Local checks cover LRU pressure, future-partner capacity, per-owner/global bounds,
restart, deadline expiry/non-renewal, source changes, explicit invalidation,
concurrent publication in separate processes and storage backpressure. Tiny real
CSV calculations prove standalone-to-pair reuse after pressure/restart with
identical published results. Mode-switch and selective-source retirement checks
additionally cover preserving another alternative's valid baseline.

The native PostgreSQL run passed **49 checks** (11 automatic-preparation, 25
Visualiser and 13 Results), with disposable-database cleanup confirmed:
`/tmp-codex/visualiser-summary-retention-20261017-092122/report.json`.
This verifies real completion receipts, permission snapshots, standalone expiry
and release after pair publication. Subsequent on-demand-mode and selective-source
retirement refinements are covered by the local planner regressions; the database
snapshot queries did not change. Hosted mixed-load acceptance remains separate
from these functional retention checks.

Final local evidence:
`/tmp-codex/visualiser-summary-retention-final-20261017-093314/report.json`.
The suite ran 157 cases: 156 passed and the historical-runner-only guard was
skipped against the current build. That guard separately passed against
`visualiser-build-20261011-cache-b`, recorded in the linked compatibility evidence.
The current run includes 17 platform retention-planning checks, 14 cache/hold
checks, real tiny CSV/publication parity, cross-process writers, operator privacy,
capacity packaging and scheduling/remote-facade regressions. No scientific
simulation was launched. Both repositories pass `git diff --check`.

### Finished chart data, sizes and retention — 16 October 2026

The finished data is distinct from the temporary per-seed summary cache. Removing
an intermediate summary does not itself remove a ready comparison. However,
the current ready-comparison policy still expires publications after 24 hours
in the candidate configuration, and serving a ready publication rechecks that
its original source results remain available. Raw-output deletion therefore
also makes those charts unavailable. This is the current implementation, not
the desired longer-lived result policy below.

The native 52-year summary-replay trial recorded these finished file sizes per
baseline/alternative pair, using ten synthetic seed labels:

| Saved component | Bytes | MiB |
| --- | ---: | ---: |
| Complete aggregate publication | 142,583,453 | 135.98 |
| 404 selected-chart section files | 131,098,435 | 125.03 |
| Section index and catalogue | 92,092 | 0.09 |
| **Total per pair** | **273,773,980** | **261.09** |
| **Baseline with two alternatives: two pairs** | **547,547,960** | **522.18** |

Source: `tasks[0].parts` in
`/tmp-codex/visualiser-capacity-bounded-20261016-001241/round-1-3072m-1000cpu-1workers/cold/worker-1/report.json`.
These totals sum the immutable publication and helper-verified index/section
receipts; they exclude small job/selection receipts, transient working files,
the separate per-seed summary cache and filesystem overhead. Production keeps
both the full publication and its indexed sections. The benchmark removed the
bulky files after fingerprinting them. Each pair currently repeats the baseline;
two alternatives therefore use approximately twice the storage of one pair.
The browser renders charts from these rows; it does not need stored PNGs for
each chart, nor does it download all these files to display one selected chart.

**Current user downloads:** the Visualiser exports selected-chart data as CSV
(including time series, level estimates and paired differences) and chart images
as PNG. Its **CSV (all)** control covers the panels for the selected variable/
breakdown and enabled scenarios, not every variable in the prepared comparison.
Results-page raw-output/comparison ZIPs are a separate download with separate
permissions. The new hosted build also provides a complete aggregate comparison
download and matching local reader, described below. It does not include an
offline copy of the interactive application.

**Implemented portable export/import — 16 October 2026:** the hosted adapter
provides **Download comparison data** and **Open saved comparison** together.
The download includes every available variable, breakdown and year for the
selected baseline and alternatives, independently of which chart/scenarios are
currently visible. It preserves stable
scenario IDs/names, seed metadata, level estimates, paired differences,
confidence intervals, suppressed/missing values and the calculation/schema
version. It exports already approved finished data without reparsing raw files
or including private paths, credentials or person-level records.

The server streams a compressed ZIP containing `manifest.json`, approved chart
indices and their JSON sections. It omits the duplicate complete publication;
all its chart rows are retained in the indexed sections. The separate local
reader uses an extracted folder and reads only the indices/sections needed for
the current chart. It does not parse the complete aggregate dataset into RAM.
The existing **Visualise Locally Saved Data** control continues to read raw
Person/BenefitUnit CSV folders; **Open saved comparison** handles the prepared
format and never calls that raw parser.

Download steps: open an authorised ready online comparison, choose **Download
comparison data**, and extract the ZIP. In the hosted Visualiser choose **Open
saved comparison**, then select the extracted folder containing `manifest.json`.
The public application shell at `/visualiser/local` can also reopen a package
without an existing online comparison or a signed-in session. No selected local
file is uploaded. The File System Access folder picker requires a compatible
browser and a secure origin (HTTPS, or localhost for testing).

This package recreates the saved comparisons using a compatible Visualiser,
including after server-side expiry. It does not run new simulations or derive
new seed-paired baseline comparisons from pooled statistics alone. A fully
offline copy of the application is a separate packaging feature; a data archive
does not itself contain the interactive application. The data download remains
subject to the current source/owner checks while being delivered. A completed
download is an intentional local copy; later server expiry cannot erase it.

The package format is `simpaths.visualiser.package.v1`: a catalogue, a list of
checksum/byte-bound part indices, the application hash, and selected calculation/
model/prepared-input identities. Local reads verify file sizes, SHA-256 hashes, selected-configuration
metadata and the closed aggregate row schema. Manifest/index limits are 1/16 MiB;
selected charts retain the online 8-MiB/20,000-row limits. Downloads allow at most
50,000 files and 64 GiB of uncompressed chart/index bytes, bounded further by the
existing publication/cache limits. One export per execution root shares the
existing two download slots. Read locks, periodic permission/stop checks and
final authorisation prevent a changed/revoked source producing a completed ZIP;
close/disconnect releases the slot and locks. No temporary ZIP is retained.

The eight focused server tests, seven local-reader tests, existing archive/RPC
checks and compiled round-trip proof pass locally. The round-trip proof exports
using the real Python server, extracts that ZIP, opens it in the compiled React
application, and matches level/paired CSVs for two alternatives with the server
made unavailable. It verifies that unused chart sections remain unread. The new
build is `/tmp-codex/visualiser-build-20261016-portable-b`.
The native Chromium/private-RPC proof passed: it downloads a complete package
while an alternative is hidden, reopens it through real origin-private filesystem
handles, matches CSVs, denies a second owner's archive request, and reopens the saved
package after actual server-source deletion. All thirteen HTTP/remote-service
checks passed on 16 October. The browser-only rerun then passed with complete RPC
evidence and cleanup, recorded at
`~/simpaths-benchmarks/vm-visualiser-portable-retry-20261016-110048/model-proof/report.json`.
Its fictional three-configuration package contains 515 files in a 341,013-byte
ZIP. Both archive RPC requests were recorded; private processor errors are empty,
and private processors and RPC handlers stopped cleanly. This same-UID transport
proof does not replace installed-host UID isolation acceptance.
The earlier failed run remains preserved; its test-only fixture-variable collision
was corrected before the successful rerun.
No upstream message or PR has been sent.

Run the final HTTP and native private-service/browser checks with networking
enabled, outside any quiet scientific benchmark. They use fictional data and a
disposable PostgreSQL container, with no scientific simulations or emails:

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  -m unittest discover -s tests -p 'test_batch_remote_service.py' -v &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 ~/simpaths-browser-tests/venv/bin/python \
  tests/browser/batch_visualiser.py --browser-only --private-rpc \
  --build /tmp-codex/visualiser-build-20261016-portable-b \
  --output "$HOME/simpaths-benchmarks/vm-visualiser-portable-$(date +%Y%m%d-%H%M%S)"
```

The sandbox cannot complete even a minimal native thread-to-event-loop wakeup;
the unchanged HTTP test likewise waits indefinitely there. The terminal check
therefore covers both the HTTP streaming/denial case and actual Chromium,
filesystem, database and private-socket behaviour. Local checks use direct ZIP
streams, bounded RPC test wires and the compiled application with JSDOM.

Earlier genuine 100,000-person/52-year outputs used approximately 3.46 GiB per
configuration/seed. Ten repetitions extrapolate to about 35 GiB per configuration,
or 70 GiB for one baseline/alternative pair. The measured 261 MiB finished pair
is around 0.4% of that extrapolated raw size. This compares a replay measurement
with a raw-size estimate; it is **not** a ten-independent-seed raw-output trial.
Do not treat it as a universal size bound. Finished rows pool repetitions, so
their size need not increase tenfold with ten seeds; years, variables, populated
groups, alternative pairs and numeric precision matter. Population size does
not directly multiply the number of fixed aggregate cells, although it can
populate previously empty cells.

An older, genuine one-seed pair's full publication was 141,658,767 bytes
(135.1 MiB). Its 11,317,023-byte `.zst` copy is compressed **archived evidence**,
not the current live storage format. Neither this compressed size nor the tiny
selected-chart HTTP response measures the full live publication-plus-index size.

**Independent retention is implemented:** raw output, intermediate summaries and
finished comparison/chart data have separate lifetime settings. The candidate
uses seven-day summary/chart periods, configurable independently from 1–8760
hours. Changing chart data to 30 days or three days leaves raw retention
unchanged. Summary protection begins at simulation completion; chart expiry
begins at publication readiness and is visible in Results. Reads and restore do
not renew either clock. Matching durations do not guarantee matching dates when
preparation is delayed. See [the retention/recovery guide](VISUALISER_RETENTION.md)
for the exact permission and expiry rules.

The previous 8-GiB combined Visualiser cache included summaries, active work and
publications; the iterative candidate increases this to 16 GiB. It must not silently promise longer retention at arbitrary
experiment counts. The retained-result budget and retirement rules need to be
made consistent with the selected independent policies.

Migration 029 stores verified aggregate-source provenance and distinguishes
routine raw expiry from explicit deletion. Current permissions remain mandatory;
revocation or explicit deletion denies hosted charts and summary reuse. Routine
expiry permits existing charts and new comparisons from still-valid retained
summaries, without opening missing raw files. A changed calculation, absent or
expired summary cannot be regenerated after raw expiry. New offline/online
backups include committed summaries and ready publications/indexed sections,
with their fixed deadlines and matching database provenance. Keeping one finished
comparison cannot recreate every future comparison.

### Measuring eviction-led raw reprocessing

The [retained-summary RAM/CPU/worker matrix](VISUALISER_REGENERATION_TRIALS.md)
separately compares 2, 2.5 and 3 GiB per worker, 1/2 vCPUs per worker and one/two
workers for 2/5/10 repetitions. It times two independent tasks with a common
start, reporting first-result latency and total throughput. It does not measure
raw parsing or enable production workers/borrowing. The earlier focused
3-GiB/1-vCPU repetition run completed all nine trials; see
[the recorded timings](VISUALISER_REPETITION_TRIALS.md#validation-and-storage-policy-follow-up).
The full matrix subsequently passed all 108 batches / 216 tasks with cleanup
confirmed. The [native results and conclusions](VISUALISER_REGENERATION_TRIALS.md#completed-native-matrix--17-october-2026)
show a throughput benefit from two workers for retained-summary regeneration;
raw summarisation and mixed hosted contention remain separate measurements.

With private summary reuse enabled, the cache now records cumulative numeric
counts and costs in private bounded metadata. `operator_status` includes them
under `summary_cache_usage` without reading scientific data or changing files:

- Hits, misses, capacity evictions and evicted bytes.
- Misses after a known capacity eviction, explicit invalidation or a rejected
  entry, with other misses classified as **unknown**, not assumed first use.
- Genuine raw parsing attempts, completed/failed counts, elapsed wall time and
  logical CSV input bytes; a separate subset records only attempts caused by a
  known eviction. A missed lookup or summary replay never counts as raw parsing.
- Starts without a completion record remain visible after a hard interruption;
  they may be running or interrupted and have no invented completion/duration.

These counters survive ordinary process restarts and are independent of the
optional PostgreSQL operational-history collector. They start with the updated
backend; old trials cannot retrospectively supply these measurements. Counts
are cumulative since `started_ns`, not per-task deltas or hourly DB buckets.
Compare successive snapshots to measure rates; `eviction_fraction_of_raw_starts`
shows the cumulative fraction of raw attempts attributed to eviction.
Input bytes describe the offered CSV files, not physical disk reads. Timing
covers the parser command and its source-stability check, excluding preliminary
source hash verification, later pair combination and publication/indexing.

Eviction/invalidation history retains at most 1,024 hashed identities for 30 days;
age/overflow discard counts are reported. After a marker is discarded, a miss is
unknown, so eviction attribution is a **lower bound**, not a claim of complete
lifetime attribution. The private state file is capped at 512 KiB, with at most
one equally bounded temporary replacement; this metadata is additional to the
summary data/receipt byte cap and counts toward overall filesystem usage.
Operator snapshots contain no cache keys, owner names, raw paths or scientific
values. Missing/invalid measurement state is reported as unavailable rather
than zero. Updates are best effort and do not prevent scientific processing;
an update failure can leave the previous valid snapshot and its original
`updated_ns`, so interpret counts with that timestamp.

The genuine-raw capacity mode uses the same measurements. Summary-replay mode
reports zero raw parsing attempts, and reports cumulative cache usage separately
from its existing per-task hit/miss and stage timing fields. The tests use tiny
fictional CSV files and deliberately force cache eviction to verify both the
cost accounting and unchanged calculated results.

Local verification passed 89 focused checks: 25 cache-usage/cache/operator
checks, nine backend checks (the legacy-runner guard tested against its historical
build), and 55 capacity/worker/packaging checks. These include actual tiny CSV
calculations, repeated eviction with identical results, separate processes,
hard-interruption accounting, invalid/oversized tracking, immutable snapshot
imports, and raw versus replay accounting. No Docker or scientific model was
started for these checks. Full raw-output capacity and mixed hosted trials are
still pending.

## Agreed scheduling policy

- Normally prioritise eligible work in this order: **results a user is actively
  waiting for**, then **automatic preparation of verified completed results**,
  then new queued simulations. Both kinds of preparation should normally receive
  available or borrowed resources before new simulations, even when nobody has
  opened the Visualiser yet. This moves useful preparation into the interval
  between simulation completion and the user's return. Cached chart reads remain
  ordinary service requests; they do not enter this heavy-work queue.
- Do not stop or restart running simulations for preparation. Temporary CPU
  boosts may be reduced to the tested minimum as described below. Borrow RAM
  only when genuinely available: reducing a CPU quota does not release RAM.
- Define an authenticated, expiring indication of active demand. Repeated
  requests must deduplicate preparation and must not indefinitely block other
  owners' queued simulations. Automatic preparation also needs owner fairness
  and bounded priority: a continuous stream of completions must not indefinitely
  block new simulations. Use ageing of queued work and a bounded share of newly
  available capacity or another explicit progress guarantee; specify and test
  the exact policy before deployment. Do not speculate on unselected baseline
  pairs or repeat valid cached work merely to occupy available workers. A
  comparison blocked on an unfinished baseline is not runnable high-priority
  work; permit other safe work without consuming protected preparation capacity.
- Initially protect capacity for **one heavy aggregation worker**, provisionally
  1 vCPU and 3 GiB. This allocation must be available even when aggregation is
  idle; ordinary simulation admission and live growth must not consume it.
- Allow a **second worker** to borrow genuinely released simulation CPU and RAM
  after verified process termination and reservation release. Do not reserve
  this second worker's maximum capacity permanently or assume that a completed
  status alone proves resources are free. CPU may additionally be reclaimed from
  temporary simulation boosts after verifying the lower quota, provided enough
  RAM and storage are independently available.
- Initially cap heavy processing at two workers. A second worker can process
  independent configurations or suitably isolated per-seed tasks, then combine
  their summaries. Correctness, publication races and duplicate baseline work
  require explicit tests. Enable borrowing only where measurements show a useful
  reduction in time to ready charts or increased preparation throughput without
  unacceptable disk contention or website latency. A larger CPU allowance for
  one single-threaded calculation does not itself establish a speed improvement.
  Two is the current implementation/measurement ceiling, not a universal optimum.
  A future generalisation may admit more workers when total CPU, RAM, scratch
  space and measured disk performance permit, while preserving website
  responsiveness. Do not automatically create two workers for each completion;
  admit only useful independent work within the shared budgets. Parallel seeds
  within one configuration and mixed 3-GiB/2-GiB workers remain unverified.
- Release borrowed capacity when preparation finishes. Keep the protected
  SingleRun slot and general-service budget outside borrowing. Automatic
  background preparation uses the same aggregation capacity as user-requested
  preparation; it does not get another unrestricted pool.

The later [idle SingleRun CPU policy](IDLE_INTERACTIVE_CPU.md) permits CPU-only
simulation boosts with verified recall. It does not lend SingleRun RAM/storage
or add that protected CPU to Visualiser processing reservations.

Reservations and enforced limits must agree. A 3 GiB worker budget means the
whole measured processing group, including descendants and publication/index
work, rather than a 3 GiB JavaScript heap with unbudgeted overhead. Account for
the long-lived private service separately and ensure its enclosing service limit
does not inadvertently kill an otherwise correctly limited child.

### Conditional option: revocable loans from model-growth headroom

**To do; not implemented or enabled.** Reconsider lending unused emergency
model-growth memory to the optional second aggregation worker only if the planned
quiet laptop one/two-worker trials demonstrate a worthwhile improvement in time
to ready charts or preparation throughput. The first native summary-replay round
on 16 October shows a throughput benefit, as recorded below; repeat-order and
mixed-load validation remain outstanding. Confirm the benefit under mixed load
on the VPS, including disk contention, website responsiveness and discarded work.
Both the second worker and this separate loan option must remain disabled by
default; demonstrating a concurrency benefit alone does not establish safe recall.

If the measurements justify implementation:

- Lend only to disposable secondary preparation. Keep the primary worker,
  SingleRun and essential services protected. The current secondary worker needs
  3 GiB: a 2 GiB loan still needs 1 GiB of ordinary spare capacity, unless tests
  independently establish that a 2 GiB worker is sufficient.
- Recall before an expansion becomes urgent, using an earlier threshold and a
  faster monitoring path while a loan exists. Investigate retaining an unloaned
  safety margin. The current 85% working-memory trigger and ten-second check
  interval do not establish that recall would finish before a container OOM.
- Give recall priority over ordinary preparation/model admission, including the
  60-second preference window. Cancel the secondary worker, verify whole-group
  termination and available memory, then transfer the reservation and confirm
  the simulation's Docker limit increase. Never refund an uncertain allocation.
- Requeue interrupted preparation, preserve completed verified per-seed summaries,
  and prevent repeated borrowing/cancellation while growth demand persists.
- Measure pressure detection, cancellation, memory release and confirmed Docker
  expansion separately and end to end. Test rapid growth, simultaneous requests,
  heavy disk activity, cleanup failure and service restart. Approve thresholds
  and recall deadlines from measured memory-growth rates and remaining headroom.

### Temporary simulation CPU boosts

The agreed candidate policy gives each large configuration a tested **1-vCPU
minimum**, with revocable boosts up to **2 vCPUs**. Its starting container memory
remains 5 GiB with a 4 GiB maximum Java heap. For the full 8-vCPU/30-GiB MultiRun
starting budget, the candidate allocations are:

| Active large configurations | CPU distribution | Total CPU | Starting RAM |
| --- | --- | --- | --- |
| One to four | 2 vCPUs each | 2 to 8 vCPUs | 5 to 20 GiB |
| Five | Four at 1.5 vCPUs and one at 2 vCPUs | 8 vCPUs | 25 GiB |
| Six | Four at 1.5 vCPUs and two at 1 vCPU | 8 vCPUs | 30 GiB |
| Further arrivals | Queue until admission fits | Unchanged | Unchanged |

Choose boost recipients according to time waiting for additional CPU while
maintaining fairness between owners, rather than always favouring the newest
arrival. Specify the exact waiting-time accounting, tie breaks and a minimum
interval between adjustments before implementation. Recompute on meaningful
changes in demand or released capacity, avoiding continual quota oscillation.
Prioritised aggregation receives borrowed capacity before simulation boosts or
new model admission; the table is not an entitlement when aggregation borrows
from that budget. Protect the interactive and general-service allocations.

For example, when a 1.5-vCPU configuration finishes with a queue waiting, use
0.5 vCPU to promote an eligible existing 1-vCPU configuration and 1 vCPU to start
the next configuration, if higher-priority preparation and all other resource
checks permit it. If demand falls, remaining simulations may receive up to
2 vCPUs each. Withdraw boosts when necessary to admit new work or useful
aggregation, without lowering any running simulation below its tested minimum.

The implementation records durable CPU-allocation revisions and reconciles
Docker limits. Reserve capacity before increasing a quota, and verify
quota reductions before releasing capacity for another consumer. Preserve
reservations when the outcome is uncertain, reconcile after restart, and test
lost replies, failed updates and completion/cancellation races. These changes
are not resource-exhaustion retries and must not restart the JVM. Keep the tested
Java processor-count setting at two; size runtime budgets for the 1-vCPU floor.

The 30 GiB starting budget is not sufficient for six 5-GiB jobs to grow within
a strict 30-GiB pool. Separately permit bounded use of genuinely spare shared
memory for recovery while maintaining protected service headroom. CPU boosts
do not imply additional memory reservations or guarantee simultaneous growth.
The candidate allocation is not yet a measured hosted throughput optimum.

## Workload and local test matrix

The principal acceptance workload is **100,000 initial people, all 52 annual
years from 2019 through 2070, ten genuine repetitions for the baseline and ten
matching-seed repetitions for an alternative**. Subsequently add alternatives
to test baseline reuse and publication growth. Population affects annual block
size; years, repetitions, breakdowns and alternatives affect summaries and
combination work. Streaming raw files does not make memory demand constant.

| Test | What it establishes |
| --- | --- |
| One worker at fixed 1 vCPU, with separate 2, 3 and 4 GiB whole-group limits | Completion, memory headroom and whether additional memory reduces elapsed time |
| One versus two vCPUs per worker, at the same memory and workload | Whether borrowed CPU shortens a single preparation; compare with adding a worker at the same total CPU allowance |
| Ten-seed baseline and alternative | The intended repetition count and matched-seed calculation workload |
| Multiple alternatives sharing one baseline | Reuse, aggregate growth and unchanged comparison calculations |
| The same two comparisons processed serially by one worker or concurrently by two workers | Equal-work throughput, queue-to-ready time and disk/memory contention |
| Empty versus retained bounded private summary cache, using fresh workers | Summarisation avoided, cache hits/misses/eviction and unchanged published results |
| Separate opt-in cancellation after completed summaries, followed by a cached retry | Whole-container stop/removal latency, cleanup and reusable checkpoints; not a proof of safe model-memory recall |
| Preparation and chart requests while simulations run | Resource isolation and service responsiveness under mixed demand |
| Continuous automatic preparation from several owners, with queued simulations | Deduplication, owner fairness and eventual progress for lower-priority work |
| Source removal, version changes, restart and access revocation | Private cache invalidation and safe recovery |

Keep CPU, inputs, versions and calculation settings fixed when comparing memory
limits. Disable automatic limit growth/retries for measurements of fixed limits;
record failed cases rather than silently increasing their allowance. A limit
failure should stop its bounded case cleanly and preserve diagnostic evidence.
Retain exact source hashes and record available host RAM/disk before execution.

If ten genuine seed outputs are unavailable locally, repeating verified input
files under synthetic fixture identities can exercise file volume, summary
cardinality and memory limits without generating new simulations. Label this
explicitly as a **capacity fixture**: it does not provide independent random
seeds, validate ten-seed uncertainty, or establish variation across real seeds.
Identical files can also benefit disproportionately from filesystem cache. Never
register such results as a scientific experiment or silently relabel genuine
seed provenance. Report logical bytes processed separately from physical bytes
stored and read; shared fixture files do not reproduce a 70 GiB disk working set.

Test order and filesystem cache affect timing. Record first and subsequent
passes, repeat comparisons in different orders when practical, and document
other laptop activity. Do not clear system-wide caches during ordinary use just
to manufacture a cold run. Small fixtures and unit tests establish guards and
correctness, not representative throughput or memory capacity.

## Measurements and host follow-up

Record parsing, per-seed summarisation, combination and publication/indexing
durations separately. Capture whole-group current/peak memory, CPU time and
throttling, I/O bytes, exit/OOM status, queue waiting times and input/output
sizes. Distinguish total charged memory from an estimate of working memory that
excludes reclaimable cache. Validate results against the same calculation
implementation and keep scientific values out of public diagnostic reports.

While measuring mixed workloads, collect website/chart response latency and
errors, memory pressure, storage latency and simulation completion rates. Warm
chart reads are different work from first-time aggregation and both matter.

Local tests can establish correctness, guard behaviour and initial memory
requirements. Repeat the one/two-worker and mixed-load tests on the VPS: shared
CPU contention, disk performance and host overhead differ from the laptop.
Judge additional workers by time to usable charts and overall service
responsiveness, not just CPU utilisation. Do not infer ten-seed peak memory
or hosted concurrency from the existing single-seed comparison proof.

## Outstanding work

- [x] Prepare the bounded local resource harness, fixture verification and
  guard tests. See commands below; summary-replay trials have run.
- [x] Record the first local summary-replay resource trials.
- [x] Run and record genuine raw-output resource trials with two matching
  repetitions per configuration: 24 batches passed on 17 October. Full
  ten-repetition, multi-alternative and mixed hosted trials remain required
  before approving production limits.
- [x] Measure known cache-eviction-led raw reprocessing separately from initial/
  unknown misses, invalidation and rejected entries; expose numeric costs to
  the operator and to future genuine-raw capacity reports.
- [x] Portable comparison export/import: download all available finished
  chart data with its metadata, and reopen it through a bounded local-section
  reader; local integrity/permission/stream and compiled round-trip checks pass.
- [x] Run native Chromium/private-service acceptance for the portable download
  and saved-comparison reader against the new candidate build; 16 October
  browser-only rerun passed with RPC evidence and cleanup.
- [x] Implement independently configurable raw-output and finished-comparison
  retention policies, initially using matching periods, with explicit byte
  quotas, expiry bases, user-visible deadlines and tested deletion semantics.
- [x] Support shorter or longer finished-result retention independently of raw
  output; implement provenance, permission/revocation, browser invalidation and
  backup/restore checks before allowing chart access after automatic raw expiry.
- [x] Implement pressure-driven eviction of finished charts unused for 72 hours,
  durable suppression of automatic recreation, protected reads/downloads and
  independent summary retention. Add private live approval of bounded storage
  growth and administrator warning thresholds. Local policy/adapter/browser
  checks, 90 native queue/HTTP checks and 54 recovery/configuration/runtime checks
  passed with no test skips. Evidence is recorded in the storage guide; hosted
  sizing and mixed-load checks remain separate.
- [ ] Conditional follow-up: evaluate revocable loans from unused model-growth
  headroom to a disposable second aggregation worker, subject to the performance
  and recall-safety gates above. Keep disabled until both are demonstrated.
- [ ] Measure the full workload with genuine matching seeds, including several
  alternatives; explicitly separate any local capacity-fixture evidence.
- [x] Implement automatic completion-triggered configuration preparation.
- [x] Implement explicit simulation-to-preparation capacity handover,
  continuing through ready standalone/comparison publication without a mandatory
  second queue, and releasing capacity when comparison inputs are missing. Local
  continuation, recovery and source-readiness checks pass.
- [x] Complete native handover/migration/rollback/database-backup compatibility
  checks: 123 suite and 21 runtime/shutdown checks passed with no skips and
  disposable database cleanup confirmed. Evidence and repeatable commands are
  in [VISUALISER_HANDOVER.md](VISUALISER_HANDOVER.md).
- [x] Complete the combined encrypted backup/restore rehearsal with pending and
  adopted preparation transfers. All 30 checks passed: restored admission,
  startup rediscovery, retained summary/chart access and permissions, without
  repeating completed models. See [HANDOVER_RECOVERY.md](HANDOVER_RECOVERY.md).
- [x] Retain per-seed summaries needed by pending comparisons through bounded
  dependency-aware holds; test budget pressure, expiry, restart, invalidation
  and avoidance of repeated raw parsing. Local tests and tiny real calculations
  pass; native PostgreSQL verification is recorded separately below.
- [x] Verify pending-summary dependencies through the native PostgreSQL
  completion, Results permission and publication-retirement suites: 49 passed,
  disposable resources cleaned up; evidence recorded above.
- [ ] Generalise independent preparation slots beyond two if further measurements
  justify it. Each slot can now run two seed tasks, allowing up to four raw
  parsers with full shared admission. Larger mixtures and hosted loads still
  require measurement.
- [x] Run the isolated native seed-parallel and one/two/four-worker scaling
  benchmark: 18 cases passed on 18 October; see the linked results. This tests
  fixed combined budgets, not live reservation handover or mixed hosted load.
- [x] Integrate bounded parallel seeds into the private preparation path with
  actual group admission, sequential fallback, independent request/log files,
  cooperative cancellation, joined cleanup and unchanged calculation outputs.
  The production kernel proof passed on 18 October: two production commands share
  the enforced 5-GiB/2-vCPU group and finish cleanup before release; descendant
  cleanup and OOM containment also passed. The first PostgreSQL run passed nine
  parallel-service and 25 publication/access checks, then exposed a pending-partner
  authority bug in two retention checks. The fix separates current dataset grants
  from output readiness and passed 42 focused local checks. Native verification
  then passed 12 automatic-preparation, nine independent-retention, eight
  processing-capacity and 11 adaptive-CPU checks. The shared-pool fixture now uses
  normal Visualiser initialisation; the final native run passed all 16 shared-pool
  and 21 runtime/shutdown checks, with disposable database cleanup confirmed.
  Evidence and repeatable acceptance commands are in the integration guide.
  Hosted mixed-load and responsiveness acceptance remain outstanding.
- [x] Implement durable, private, role-independent per-seed summary reuse and
  invalidation/retention rules.
- [x] Implement selected-baseline comparisons for every ready alternative.
- [x] Implement bounded priority for active-user and automatic preparation before
  new simulations, protected aggregation capacity and borrowing for a second
  worker; align reservations with enforced limits and provide bounded model
  admission opportunities when resources fit. Targeted native enforcement checks
  passed; hosted mixed-load acceptance remains outstanding.
- [x] Implement fair, revocable 1-to-2-vCPU simulation boosts, durable allocation
  reconciliation and suitable minimum-CPU runtime budgets.
- [ ] Measure dynamic transitions and mixed hosted load separately from the
  fixed-quota trials.
- [ ] Test publication, restart and permission behaviour under concurrent work.
- [ ] Repeat representative concurrency and responsiveness measurements on the
  VPS before approving memory limits and maximum worker count for production.

## Prepared laptop harness — 15 October 2026

`visualiser_capacity.py` runs disposable, labelled Docker workers. Each worker
has **one or two enforced vCPUs**, a fixed whole-cgroup memory limit, no swap,
no network, a read-only root and read-only source mounts. Only its own evidence
directory and the case's private bounded summary cache are writable. No
simulations, database, emails or production service are launched.
Code is snapshotted before execution; the image ID, code/data hashes and Python,
Node and kernel versions are recorded. Containers are removed after each case.
Bulk intermediates are reclaimed after success or failure; small reports and
samples remain. No Docker-wide cleanup or image pruning is performed.

Use the new **`compare`** command for the current design. The original `run`
command is retained for historical reproduction: it processes one whole
comparison per worker through the older combined publication. It gives two
workers twice as much work and does not exercise private summary reuse.

The updated matrix is 3, 2 and 4 GiB per worker, one/two vCPUs per worker, and
one/two workers. Every batch performs **the same two complete comparisons**:
one worker handles them sequentially; two workers handle one each. A comparison
contains a baseline and two alternatives. Each pair stays inside the unchanged
publication budget; only catalogues and selected sections are composed.
Both schedules perform the same two complete source-verification passes per
comparison, including cache hits, rather than giving serial processing fewer
raw-file hash reads.
This measures concurrent preparation throughput, not parallel seed processing
within one comparison. The example production configuration still enables only
one worker. Node's
old-generation heap limit is total memory minus 1 GiB, and the Python helper's
address-space limit is total memory minus 256 MiB. Both run inside the fixed
total cgroup cap. These exact settings are recorded, rather than confusing a
heap limit with total memory.

Each resource case first starts with an empty application summary cache, then
repeats with its retained cache and fresh worker processes. The same production
`SummaryCache` enforces a **shared 2 GiB / 256-entry** budget across concurrent
workers. Task identities remain distinct so two simultaneous experiments do not
accidentally reuse each other's work. Repeated phases of the same task can reuse
their own verified summaries. Cache hits, misses, computed/restored bytes, lock
waits and retained sizes are recorded; a warm phase can still miss after eviction.
The original summaries are stored once; synthetic alternatives do not add
independent policy evidence.
The harness batches an experiment's summaries before building its pairs. It
uses the production cache/calculation/publication helpers, but does not reproduce
the dispatcher's exact standalone/pair job sequence or all of its extra cache
copies and source checks. Hosted service acceptance must measure that job mix.

Stages separately record summary replay/raw parsing or restore, combination,
validation, publication, indexing, catalogue and repeated selected-chart reads.
Reports include each pair's ready time, full-comparison ready time, task waiting
time, initial/final verification time and local chart-helper read durations
(including their small sample count). Waiting time runs from batch submission
to the start of that task's calculations, including initial verification where
applicable. JSON reports also give throughput per reserved CPU-hour/GiB-hour
and minimum sampled host available memory/free disk. Samples include
charged/working memory, cgroup peak, OOM events, CPU use/throttling, block I/O and
pressure. The controller and its descendants are measured together. The full
HTTP/private-service stack and browser are not running; these chart-helper
timings do **not** establish website latency. Batch throughput includes container
startup, source verification and successful workers' scratch cleanup; host
container removal and interrupted-work cleanup are recorded separately. Reports
distinguish sampled simultaneous memory from the sum of individual kernel peaks.
Matched tables compare one/two workers at the same per-worker CPU and, separately,
one 2-vCPU worker versus two 1-vCPU workers. The latter holds total CPU constant
but doubles the RAM reservation. Evaluate that trade-off before choosing a
throughput policy. Successful runs of each task must have identical publication,
index, catalogue and view hashes across resources/cache states. Failed memory cases retain evidence and the
remaining cases continue; unconfirmed container removal stops the sequence.

### Available fixture and scope

The old raw research CSVs were deliberately disposed after verification. The
remaining compressed, hash-verified per-seed summaries permit **summary replay**:
the same baseline and alternative metrics are assigned ten synthetic matched
seed labels, explicitly separated from their original seed 606 provenance.
This exercises the real calculation and publication code with ten-entry seed
maps, but cannot validate ten genuinely independent repetitions, raw CSV parsing,
the 70 GiB disk working set, or scientific uncertainty across different seeds.

Prepared fixture:
`/tmp-codex/visualiser-capacity-fixture-20261015/manifest.json`.
It contains two decoded summary files totalling about 61.3 MiB, stored once, and
references them with ten synthetic labels. It uses
`/tmp-codex/visualiser-build-20261010-selective-e`, whose calculation and runner
hashes match the source evidence. Later Visualiser builds are not interchangeable
with this provenance. A changed scientific calculation requires new summaries
from raw data. `rebind-replay` explicitly permits a new bundle/runner only when
the original and new build have identical `parseCore.js`, `useAggregatedData.js`
and `csvParse.js` hashes, calculation dependencies and toolchain versions. It
verifies all build/source files and writes a new fixture recording both build
receipts and the original provenance. It never edits the old fixture or build.

Prepared updated fixture:
`/tmp-codex/visualiser-capacity-fixture-20261015-bounded-pairs/manifest.json`,
using `/tmp-codex/visualiser-build-20261015-preparation-a`. It adds a second
synthetic alternative without copying another source summary. All three
scientific source hashes, dependencies and toolchain versions match the original.

To recreate that updated fixture in a **new** directory:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/visualiser_capacity.py rebind-replay \
  --fixture /tmp-codex/visualiser-capacity-fixture-20261015 \
  --source-build /tmp-codex/visualiser-build-20261010-selective-e \
  --build /tmp-codex/visualiser-build-20261015-preparation-a \
  --alternatives 2 \
  --output /tmp-codex/visualiser-capacity-fixture-bounded-NEW
```

To recreate the fixture in a **new** directory if necessary:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/visualiser_capacity_fixture.py \
  --archive /tmp-codex/research-comparison-aggregation-20261010 \
  --build /tmp-codex/visualiser-build-20261010-selective-e \
  --repetitions 10 --alternatives 1 \
  --output /tmp-codex/visualiser-capacity-fixture-NEW
```

### Build the small test-runtime image

Do this with networking enabled, **before** the quiet measurements:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
docker build \
  -f deploy/multirun/visualiser/Dockerfile.capacity \
  -t simpaths-visualiser-capacity:20261015 \
  deploy/multirun/visualiser
```

This is a benchmark image containing Python and Node, not a new model image or
production release. Its runtime inputs are mounted read-only and it does not
contain a model JAR. Building requires network access; running the prepared
summary trials does not.

### Quiet laptop measurements

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/visualiser_capacity.py compare \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --build /tmp-codex/visualiser-build-20261015-preparation-a \
  --fixture /tmp-codex/visualiser-capacity-fixture-20261015-bounded-pairs \
  --memory-mib 3072 2048 4096 --workers 1 2 --cpu-millis 1000 2000 \
  --tasks-per-case 2 --cache-modes cold warm --rounds 2 \
  --output "/tmp-codex/visualiser-capacity-bounded-$(date +%Y%m%d-%H%M%S)"
```

The two-worker cases require at least **8 GiB free disk** and available RAM equal
to the workers' combined caps plus **2 GiB host headroom**; the 4 GiB/two-worker
case therefore requires 10 GiB available RAM. These checks run before each case.
They are test admission margins, not calibrated production reservations. At
preparation time the laptop had about 16 GiB free disk. The sequence uses the
same exclusive lock as the research/SingleRun benchmarks when their output
parent is `/tmp-codex`; do not run other heavy work alongside it.

Default case timeout is three hours; `--timeout-seconds` can set 60–43,200 seconds.
Use `--memory-mib 3072 --workers 1 --cpu-millis 1000 --rounds 1` for a first
isolated cold/warm pair. The full command has **48 batches**, each completing
two comparisons. Its duration has not been measured: it can exceed one night.
`--rounds 1` runs 24 batches for an initial survey; use the second round before
drawing conclusions from small timing differences. Alternate rounds reverse
memory, CPU and worker ordering. File caches are not cleared. Checksumming and previous cases warm caches,
so interpret logical bytes and measured physical block I/O separately. Two
workers share the same fixture inputs, which can favour cached reads.
Cold/warm labels refer only to the private summary cache. In fresh containers,
filesystem-cache pages can remain charged to an ancestor after earlier workers
exit; charged/working memory and host admission margins should be read together.

### Optional cancellation and cached-retry measurement

Add `--cancellation-check` to `compare` to run a **separate check after all timing
batches**. It uses a new cache and one 3 GiB/1-vCPU worker, waits until its completed
summaries reach pair combination, forcibly stops the container, confirms exit
and removal, cleans scratch, then retries the same task using retained summaries.
The retry's publication/index/catalogue/view receipts must match an uninterrupted
task from the timing matrix and must demonstrate summary-cache hits.
`--cancellation-memory-mib 2048` or `4096` selects another measured worker limit.

The separate report records kill-and-wait time, time through confirmed removal
and scratch cleanup, last checkpoint resource counters, and host available RAM
before/after. An OOM, missed cancellation stage or uncertain removal cannot pass.
This intentionally exercises abrupt cancellation after verified checkpoints; it
does not measure every parsing stage or graceful production cancellation.
It **does not** test memory-pressure detection, live model growth, reservation
transfer, a Docker memory increase, or recall under mixed disk pressure. Those
remain acceptance requirements for the disabled emergency-memory loan option.

### Genuine raw-output measurements, when outputs are available

This mode verifies original completed calibration reports, actual seed plans,
input/model identities and CSV hashes. It mounts only the retained-output
directories read-only; it does not copy microdata or run simulations. It parses
each native seed using the pinned production runner before the same combination,
publication and chart checks. Sparse cells may have fewer than all seeds; those
counts must remain valid rather than being artificially filled.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/visualiser_capacity.py prepare-raw \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --prepared /tmp-codex/research-prepared-100000-20261009-232734/package \
  --baseline /path/to/completed-baseline-trial \
  --alternative /path/to/completed-lower-saving-rate-trial \
  --repetitions 10 \
  --build /tmp-codex/visualiser-build-20261015-preparation-a \
  --output /tmp-codex/visualiser-native-fixture-NEW
```

Those two trial paths are placeholders, not currently available data. The
sources must contain verified 100,000-person outputs through 2070 with the
requested actual repetition count. Then use `compare` with this
new fixture. A one-seed native pair is also supported with `--repetitions 1` but
does not certify the ten-seed workload. Full genuine ten-seed native acceptance
remains to do, preferably where sufficient retained-output storage is available.
Fresh raw-mode workers rehash all sources before and after processing, including
in warm-summary phases. This deliberately conservative verification differs from
the long-lived production source-hash cache; its timings are recorded separately.

### Known multi-alternative publication bound

The retained baseline has 135,095 summary cells and the alternative 135,017.
A replay with two alternatives therefore produces approximately **405,129**
aggregate rows, exceeding the existing **400,000-row** combined-publication cap.
`--alternatives 2` or `3` can create stress fixtures. The original capacity
harness's historical `run` command exercises a monolithic publication and can
hit this bound; it does not silently raise production limits. The `compare`
command now exercises the new service composition: separate bounded pairs and
merged catalogues/selected chart sections.

- [x] Replace the requirement for one ever-larger combined publication with
  bounded per-configuration/per-pair results and selected-chart delivery as part
  of the reusable-summary design. Fictional tests exceed the combined row cap
  while validating independent pairs and selected-section parity.
- [ ] Rerun genuine multi-alternative service and resource acceptance.

### Validation of the harness

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PYTHONPATH="$HOME/git/JAS-mine/JAS-mine-web" \
SIMPATHS_VISUALISER_TEST_BUILD=/tmp-codex/visualiser-build-20261015-preparation-a \
~/simpaths-browser-tests/venv/bin/python -m unittest \
  deploy.multirun.test_visualiser_capacity_fixture \
  deploy.multirun.test_visualiser_capacity \
  deploy.multirun.test_visualiser_capacity_worker \
  deploy.multirun.test_visualiser_capacity_comparison \
  deploy.multirun.test_visualiser_summary_cache.SummaryCacheTests -v
```

Guard tests use tiny fixtures and mock Docker/cgroup state. A tiny 52-year
fixture has also exercised the actual pinned calculation and publication,
index, catalogue and view helpers. Neither check is whole-cgroup capacity
evidence. Native Docker resource trials require the user's terminal because
the development sandbox cannot access the Docker daemon.

Updated checks on 15 October: **70 tests passed**: the original 36 harness
checks, 28 new comparison/integration checks and six private-summary-cache
guards. Tiny real helpers exercised cold/warm checksum parity, two bounded pairs
and their composite views, verified raw parsing for a single pair, interrupted
combination with checkpoint reuse, and the full worker's receipt/cleanup path
with fictional cgroup files. Docker failures, OOMs, missed/uncertain cancellation,
partial starts, exact task counts and cache-state/order comparisons have guards.
The cache's public-permission fixture now sets its intended permissions explicitly
so a private caller umask cannot accidentally turn it into a private directory.

Evidence: `/tmp-codex/visualiser-capacity-checks-20261015-bounded/report.json` and
its copied test log. The updated fixture's complete source hashes and both
scientific-build source inventories match. These checks do not start Docker or
establish native memory limits, cancellation latency or a safe growth deadline.
The earlier Docker SDK option-construction check also passed without contacting
the daemon.

### First native resource comparison — 16 October 2026

The [native report](/tmp-codex/visualiser-capacity-bounded-20261016-001241/report.json)
and [comparison](/tmp-codex/visualiser-capacity-bounded-20261016-001241/comparison.md)
passed **24 batches in one round**, completing 48 experiment-comparison tasks.
Each task compares one baseline with two alternatives in separate bounded pairs.
All task identities/publication hashes matched across resource and cache settings;
all containers were removed, with no OOM events/kills or cleanup errors. The
separate cancellation/cached-retry check also passed. The run started at
00:12:41 BST and its final report was written at 01:22:47 BST, about **70 minutes**;
summed timing-batch duration was 67.9 minutes. The original two-round command
would run 48 batches; this report contains only round one.

These are native Docker/cgroup measurements of **summary replay**, not ten
independent model repetitions or raw-output parsing. The fixture repeats verified
52-year source summaries under ten synthetic seed labels and copies the one
original alternative. There was no running website or scientific simulation
load. Cache labels describe the private summary cache; filesystem caches were
not cleared.

At **3 GiB per worker**, equal total CPU gives:

| Arrangement | Total CPU allowance | Total RAM reservation | Two tasks finished, cold | Two tasks finished, warm | First pair ready, cold / warm |
| --- | ---: | ---: | ---: | ---: | ---: |
| One worker, 2 vCPUs | 2 vCPUs | 3 GiB | 219.5 s | 170.3 s | 69.1 / 45.0 s |
| Two workers, 1 vCPU each | 2 vCPUs | 6 GiB | 145.7 s | 109.9 s | 90.5 / 56.8 s |

Two workers increased throughput by **50.6% cold / 54.9% warm**, finishing the
same batch **33.6% / 35.4% sooner**, with twice the RAM reservation. They processed
independent tasks concurrently, not parallel stages of one calculation. The
earliest pair appeared later with two workers, so this is not evidence that
every actively waiting user's first result becomes faster. A first whole task
was ready at 109.2/85.4 s with one worker versus approximately 144.4/108.9 s with
two; the benefit was earlier completion of the queued work as a whole.

Other observations from the matched cases:

- **2 GiB passed all cases**, but elapsed times were about 6–16% longer than at
  3 GiB. It is a candidate for a constrained worker, not an approved minimum for
  genuine raw workloads or a tested mixed 3-GiB/2-GiB worker arrangement.
- **4 GiB changed elapsed times by less than 3%** relative to 3 GiB, including a
  few slightly slower warm cases. There is no measured reason in this first
  round to prefer 4 GiB for this fixture.
- Raising one worker from 1 to 2 vCPUs at 3 GiB shortened its batch by about
  10–11%; raising both workers did so by about 7%. Extra worker concurrency gave
  a larger gain than extra CPU quota for one worker, subject to sufficient RAM.
- Warm private summaries shortened the 3-GiB batches by about 22–25%. All 60
  summary lookups hit in each warm batch, versus 60 misses in each cold batch.
- Sampled working memory at 3 GiB was approximately 1.43–1.50 GiB for one worker
  and 2.79–2.84 GiB combined for two. Total charged memory could reach the caps
  because it includes file cache; working peaks are not safe container limits.
- Minimum sampled host headroom was 10.83 GiB available RAM and 5.48 GiB free
  disk during work. Per-case admission still checked the required free space;
  intermediates/cache state were reclaimed between completed resource groups.

The optional cancellation check stopped a worker at the first pair's aggregate
stage in **0.34 s**, with confirmed removal and scratch cleanup **0.91 s** after
the request. Its retry reused completed summaries and matched uninterrupted
results. This measures one quiet cancellation point, not memory-pressure
detection, memory reservation transfer, Docker model growth, or safe recall
under mixed disk/CPU pressure. Emergency-memory loans remain unimplemented and
disabled.

**Candidate direction, without changing production settings:** keep 3 GiB and
1 vCPU as the primary worker's starting allocation; consider a second independent
1-vCPU worker when RAM and other capacity permit. Before enabling it, repeat in
reverse resource order, measure the real source/standalone/pair workload and
test urgent-user priority plus website responsiveness on the VM under simulation
and disk load. Tiny local chart-helper reads are not website response timings.

### Genuine raw-output resource comparison — 17 October 2026

Evidence: [native report](/tmp-codex/visualiser-raw-20261016-171532-results/report.json),
[generated comparison](/tmp-codex/visualiser-raw-20261016-171532-results/comparison.md),
and the verified fixture at
`/tmp-codex/visualiser-raw-20261016-171532-fixture/manifest.json`.

Two new configurations, baseline and lower-saving-rate, each completed two
genuine repetitions with seeds **606 and 607**, initial population **100,000**
and all **52 annual years, 2019–2070**. Both used the same pinned model runtime
as the preceding research trials, 4 GiB maximum Java heap, fixed 5 GiB container
RAM and a 2-vCPU quota. Each completed in one attempt with no limit increases;
the model containers ran for 2 h 58 min each. The complete model proofs took
about 3 h 10 min each, including their verification/cleanup. Retained output was
**6.930 GiB per configuration**, **13.861 GiB in total**; working storage peaked
at approximately 9.304 GiB per configuration.

The raw fixture mounts the original Person/BenefitUnit CSVs read-only, rather
than copying them. Selected source CSVs total **13.599 GiB**. Each benchmark task
prepares one baseline/alternative comparison using both genuine seeds. The two
tasks use distinct experiment identities over the same saved raw files; this
forces separate summary-cache entries while permitting shared filesystem-cache
effects. They are concurrent complete comparisons, not parallel stages of a
single comparison or additional scientific repetitions.

All **24 batches / 48 tasks** passed across 2/3/4 GiB per worker, 1/2 vCPUs per
worker, one/two workers, and cold/warm summary caches. Each task's result
identity matched across settings, original sources remained unchanged, and all
36 worker containers were cleaned up with **zero OOM events/kills**. This is
calculation/output equivalence across resource settings, not an independent
validation of the model's scientific assumptions. There was one timing round,
in the fixed 3/2/4-GiB resource order, with no running website or other simulation
load and no cancellation/recall trial in this run.

Aggregation started at **23:36:53 BST on 16 October**; the final report was
written at **06:08:34 BST on 17 October**, about **6 h 32 min**. Summed timing
batches took 6 h 31 min. Including generation of the four scientific runs and
fixture verification, the full command took approximately **12 h 53 min**.

At **3 GiB per worker**, the observations were:

| Worker arrangement | Total CPU allowance | Total worker RAM allowance | Both tasks finished, cold | Both tasks finished, warm | First pair ready, cold |
| --- | ---: | ---: | ---: | ---: | ---: |
| One worker, 1 vCPU | 1 vCPU | 3 GiB | 39.16 min | 3.21 min | 18.91 min |
| One worker, 2 vCPUs | 2 vCPUs | 3 GiB | 33.34 min | 3.16 min | 16.15 min |
| Two workers, 1 vCPU each | 2 vCPUs | 6 GiB | 22.95 min | 1.96 min | 21.97 min |
| Two workers, 2 vCPUs each | 4 vCPUs | 6 GiB | 20.37 min | 1.92 min | 19.56 min |

The two-task elapsed columns include source verification before/after the tasks
and worker completion. First-pair timing starts with the batch and includes
initial source verification, but excludes the later final verification. These
are preparation timings, not browser response times or time to reopen an
already-finished chart publication.

**CPU and concurrency:** with the same total 2-vCPU allowance, two 1-vCPU
workers finished both cold tasks **31.2% sooner**, corresponding to **45.3%
greater throughput**, than one 2-vCPU worker. The warm figures were **38.1%
sooner / 61.6% greater throughput**. The RAM allowance doubled from 3 to 6 GiB.
However, the first pair was **36.0% later cold** (21.97 versus 16.15 min) and
24.8% later warm. This supports two workers for useful queued work when RAM
permits, with a deliberate user-priority policy; it does not show that starting
a second worker accelerates an urgent user's first result.

Increasing one worker from 1 to 2 vCPUs reduced its cold batch duration by
**14.9%**. Increasing both workers from 1 to 2 vCPUs each reduced their cold batch
by **11.3%**. The corresponding warm improvements were only about **1–2%**.
There is therefore a modest measured cold-processing benefit to additional CPU
quota, with no proportional acceleration. It must be weighed against CPU taken
from simulations and other work.

**Memory:** all 2-GiB cases passed, but took **4.3–17.2% longer** than the
corresponding 3-GiB cases. At 4 GiB, elapsed times ranged from 0.5% faster to
5.7% slower than 3 GiB; this round gives no consistent reason to prefer 4 GiB.
The harness changes Node's old-space ceiling alongside worker RAM: 1/2/3 GiB
old-space for 2/3/4-GiB workers, respectively. This is a comparison of complete
worker resource profiles, not an isolated change to file-cache capacity.

At the 3-GiB allowance, sampled working memory was about **1.15–1.51 GiB** for
one worker and **1.88–2.78 GiB combined** for two. Individual cgroups could reach
their full charged caps because charged usage includes file cache. Working
peaks are not safe replacement caps, and individually observed cgroup peaks do
not establish simultaneous combined usage. The minimum sampled host margins
across the matrix were approximately **11.00 GiB available RAM** and **10.82 GiB
free disk**. Two genuine seeds do not establish the memory needed for ten.

**Reusable summaries:** at 3 GiB/1 vCPU, the two-task batch fell from **39.16 to
3.21 min** with retained summaries, a **91.8% reduction**. Each cold batch made
eight raw-summary calculations; each matching warm batch had eight hits and
**zero additional raw parses**. Raw parsing accounted for approximately 34.9
of the 39.2 cold minutes in the one-worker/1-vCPU batch. Cumulative shared-cache
counters carry the previous cold phase forward; compare snapshots for phase
deltas rather than adding snapshots from two workers.

Warm does not mean no raw-file reads. The harness deliberately rehashes all
sources twice per task even on summary hits. Total verification took about
128 seconds cold and 126 seconds warm for the one-worker/1-vCPU batch. The two
warm task calculations themselves took approximately **32 and 33 seconds**.
Production source-hash memoisation may avoid repeated full reads for unchanged
sources, but this trial does not measure that saving or HTTP/browser latency.

The shared summary cache retained **eight entries / 243.9 MiB** for the two
experiment identities; each configuration's two seed summaries accounted for
about **61.0 MiB**. There were **no evictions**, and therefore no eviction-led
raw reprocessing in this workload. This confirms the accounting at zero churn,
not the adequacy of a 2-GiB cache under production churn. One finished pair used
approximately **140.35 MiB** for its complete publication, **129.38 MiB** for
404 chart sections, and **0.11 MiB** for index/catalogue, about **269.84 MiB**
in total, excluding transient files and the separate summary cache.

**Historical candidate direction before the 18 October scaling tests:** retain 3 GiB/1 vCPU as the protected primary worker,
consider a temporary 2-vCPU allowance for uncached urgent work when capacity
permits, and evaluate a second independent 3-GiB/1-vCPU worker for a backlog.
The additional worker needs actual spare RAM as well as CPU; reducing a
simulation's CPU quota does not release its RAM reservation. Keep the second
worker disabled by default pending an order-reversed repeat and hosted mixed
load/priority acceptance. Emergency-memory loans remain unimplemented and
disabled; this run did not test recall, model-growth deadlines or reservation
transfer. Retain the genuine raw sources until any planned repeat is complete,
then use verified archive/disposal tooling to reclaim them.

## Opt-in runtime assembly

`vm/throughput.toml.example` records the candidate assembly; it is not an
approved measured server profile. It leaves 8 vCPUs/30 GiB for normal MultiRun
starts, protects 2 vCPUs/5 GiB for SingleRun and 1 vCPU/2.5 GiB for the primary
aggregate worker, and withholds 2 GiB of shared model-growth headroom. The revised
pool totals are 11 vCPUs/39.5 GiB. Leave remaining host capacity for
PostgreSQL, private orchestration, public services and the OS. Check the
provider's actual usable RAM: 48 decimal GB is approximately 44.7 GiB.

The growth buffer cannot admit extra ordinary jobs. A running model, or a
resource-specific retry above its original reviewed size, may use it after
normal recovery checks. It is a shared allowance, not a separate maximum
reservation for every simulation. Primary aggregation and SingleRun holds stay
protected during growth. The public-upload SingleRun preparation-exit/memory
comparison passed on 16 October with 3/4 GiB preparation heaps, a 4 GiB
application heap and a 5 GiB container; see
[the measured comparison](../MAINTAINER_GUIDE.md#first-native-memory-comparison--16-october-2026).
This covers the public 50,000-person/2019–2026 workflow, not arbitrary uploads or
horizons. The user-data catalogue now requests that 4 GiB application heap and
5 GiB container, fitting the candidate SingleRun memory hold. Restart the
SingleRun services and launch fresh sessions with the verified image to apply
the setting; old catalogues or existing 8 GiB sessions still need their full
reservations. The CPU and storage holds and actual pool admission checks apply
as well.

The candidate does not rewrite model release receipts. Register an explicitly
approved resource policy using `releases.py --resource-policy` after checking
the relevant workload evidence. For the measured large workload, the candidate
is a 4 GiB heap/5 GiB container and working storage of 4 GiB plus 4 GiB per
repetition. Existing two-repetition trials do not certify all policies or ten
repetitions. Use the existing versioned workload profiles to bound population,
years, repetitions and collector settings; reject unmatched configurations.

New pool protection settings are immutable once configured. Use a new pool ID
and a controlled drained migration rather than silently changing existing pool
or review budgets. Set SingleRun's `VM_SHARED_POOL_ID` to that same ID when the
services share this capacity. Actual disk admission checks remain required;
completed retained outputs, uploads, download archives and caches still occupy
the filesystem after execution reservations are released.

The scheduling implementation has these explicit bounds:

- CPU floors are 1 vCPU; temporary boosts use half-vCPU steps up to 2 vCPUs.
  Waiting credit and owner fairness are reconsidered every 30 seconds by
  default, including when membership is unchanged. A lower Docker limit must
  be verified before its reservation shrinks. Lost replies, restarts and
  cancellations retain the conservative reservation until reconciliation.
  Boosts never create a new attempt or change the JVM processor count, heap,
  seeds, inputs or runtime deadline. Runtime budgets must allow the 1-vCPU case
  (the example allows 15 minutes setup plus four hours per repetition).
- Authenticated demand expires after five minutes unless refreshed. Active
  preparations lead; automatic tasks older than 15 minutes join that priority
  class. Owner round-robin dispatch history survives service restart.
- Runnable preparation normally precedes new models. After 60 seconds of that
  preference, queued models receive an admission opportunity. Extra aggregate
  workers pause new borrowing until one model is admitted or no eligible model
  remains. The protected primary worker continues in its own allowance. A model
  still waits if RAM, disk, permissions or its owner's active allowance prevent
  admission; a CPU reduction cannot free memory.
  Work waiting behind an already full set of aggregate workers does not idle
  otherwise available model capacity; no additional preparation can start then.
- The revised candidate enables two independent preparation slots, each with
  up to two seed tasks. Larger groups require their full separately admitted
  CPU/RAM/scratch allowance; a failed larger request falls back to the ordinary
  sequential group. Individual JavaScript calculations remain single-threaded.
- The example allocates 2 GiB of scratch per active worker, an 8 GiB persistent
  summary allowance, and a 16 GiB total Visualiser allowance including both.
  Completed-scenario summaries and finished chart files each have a separately
  configured seven-day period. See [iteration and retention](EXPERIMENT_ITERATION.md).
  Byte/entry caps, source identity checks and private
  retirement apply throughout. These disk and memory budgets still require
  genuine ten-seed measurement.

With `hard_limits = true`, install the reviewed
`vm/simpaths-executor-resources.conf.example` as the private executor's systemd
drop-in. It delegates only that service's cgroup subtree. The orchestrator moves
to a bounded manager leaf; each heavy preparation has its own enforced CPU,
whole-group memory, zero-swap and process limits. All Node and publication/index
helpers enter through an inherited directory descriptor before execution.
Scope cleanup kills surviving descendants and verifies an empty group before
returning capacity, including after interrupted work. The drop-in's enclosing
12 GiB service limit covers up to two 5 GiB helper groups and orchestration;
Docker model containers have their own separate enforced reservations.
General RPC/chart-read orchestration is accounted for outside the model pool.

Kernel delegation background: [systemd control group interface](https://systemd.io/CONTROL_GROUP_INTERFACE/)
and [Linux cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html).

### Native correctness checks

The PostgreSQL suite uses fictional jobs and tiny aggregate fixtures, with no
simulations or email delivery:

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 ~/simpaths-browser-tests/venv/bin/python \
  scripts/test_batch_queue.py \
  --test-pattern test_adaptive_cpu.py test_processing_capacity.py test_postgres.py \
                 test_resource_recovery.py test_visualiser.py \
  --output "/tmp-codex/adaptive-preparation-postgres-$(date +%Y%m%d-%H%M%S)"
```

The separate kernel proof creates tiny helper groups in a disposable user
systemd scope. It verifies fractional CPU/RAM limits, group OOM containment and
cleanup of descendants that closed inherited descriptors. It does not install
or change a production unit and does not run Docker or models:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
systemd-run --user --scope \
  --property=Delegate=yes --property=MemoryMax=4G \
  env TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/processing_scope_proof.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --output "/tmp-codex/processing-scope-proof-$(date +%Y%m%d-%H%M%S)"
```

Actual live Docker CPU transitions have two additional native cases. These use
five tiny sleeping Python containers at most, not scientific simulations. The
following command also checks automatic preparation through the real database
and service startup/shutdown. The database and containers are removed after the
proof:

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 ~/simpaths-browser-tests/venv/bin/python \
  scripts/test_batch_queue.py --docker-tests \
  --test-pattern test_visualiser_automatic.py test_adaptive_cpu_docker.py \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/vm_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements.txt" \
  --output "/tmp-codex/preparation-runtime-native-$(date +%Y%m%d-%H%M%S)"
```

### Implementation verification recorded on 15 October

| Area | Result and evidence |
| --- | --- |
| Adaptive CPU, protected capacity, queue/recovery and existing Visualiser database checks | 92 checks passed; `/tmp-codex/adaptive-preparation-postgres-20261015-153609/report.json`. |
| Additional CPU, growth and shared SingleRun admission checks | 35 checks passed (11 CPU, eight processing, 16 shared-pool). The enclosing run stopped later on a test-fixture SQL lookup error in one automatic-preparation case; `/tmp-codex/preparation-runtime-native-20261015-160040/report.json` preserves that original failure. The corrected case passed in the focused rerun below. |
| Automatic preparation, live Docker CPU changes and service shutdown | All 25 checks passed: three automatic-preparation, two Docker CPU and 20 runtime/shutdown checks; `/tmp-codex/preparation-runtime-native-retry-20261015-162152/report.json` and its `model-proof/report.json`. Verified same-container CPU changes, conservative recovery after a lost update reply, automatic standalone/baseline preparation, permission invalidation and joining both private worker slots. Disposable resources were cleaned up. |
| Immediate completion delivery follow-up | 25 local checks passed (seven scheduler, 18 preparation), including completion-before-admission ordering, late baseline completion, bounded event draining, restart replay and stale-version rejection. Native migration, automatic-preparation, notification and retry suites passed with cleanup confirmed; `/tmp-codex/visualiser-immediate-completion-20261015-175056/report.json`. |
| Whole-helper kernel limits | Passed fractional CPU/RAM limits, zero swap, group OOM containment and cleanup of descendants that closed inherited descriptors; `/tmp-codex/processing-scope-proof-20261015-160143/report.json`. |
| Concurrent preparation, immutable selections and bounded pair composition | 14 functional checks passed; `/tmp-codex/preparation-functional-tests-20261015.log`. |
| New calculation build, summary reuse, mutation rejection and configuration | 35 checks passed with one historical-runner-only case skipped; `/tmp-codex/preparation-config-backend-tests-20261015-final.log`. New build: `/tmp-codex/visualiser-build-20261015-preparation-a`. |
| Results progress and maintained compiled charts | Results JavaScript checks and all three compiled application/bundle/runner test files passed; `/tmp-codex/preparation-results-js-20261015.log` and `/tmp-codex/preparation-compiled-js-20261015.log`. |

The sandbox cannot provide delegated kernel control files or start the
disposable PostgreSQL Docker container; the native checks above run from the
operator's terminal. Installed service/drop-in and distinct-UID acceptance,
genuine ten-seed/multi-alternative processing, CPU changes under hosted concurrent
load, and throughput/disk-contention measurements remain required before rollout.
The completed correctness checks do not establish production capacity or an
optimal allocation.
