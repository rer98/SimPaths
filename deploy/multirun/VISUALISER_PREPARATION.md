<!-- (C) Copyright 2026, by Ross Richardson

Agreed automatic Visualiser preparation design and resource-validation plan.
@author ross richardson
-->

# Automatic Visualiser preparation and resource tests

**Decision record: 15 October 2026. Opt-in implementation; verification evidence and outstanding capacity/host acceptance are recorded below.**

The agreed scheduling and reuse behaviour is implemented behind explicit operator
settings. Existing deployments retain their fixed allocations and on-demand
preparation until enabled. The implementation checks below distinguish functional
verification from the outstanding resource measurements and host acceptance.

## Current behaviour and evidence

- Results-page **Prepare Visualiser** requests still work. With automatic mode,
  the private dispatcher consumes durable completion events before admitting
  replacement simulations. It queues standalone results and each ready selected
  baseline/alternative pair without waiting for the whole experiment or the next
  periodic scan. The bounded 60-second scan remains a recovery mechanism.
- Preparation processes configurations and repetitions sequentially. Heavy
  calculations principally run in a single JavaScript thread. Increasing a
  worker's CPU allowance alone does not establish proportional acceleration.
- The server prepares aggregates and selected-chart sections. Authenticated
  catalogue/section requests and bounded browser caching avoid repeatedly
  downloading the complete publication.
- A new verified runner supports private role-independent per-seed summaries.
  Opt-in reuse checks source descriptors/hashes, owner and immutable execution
  identities. The bounded cache uses atomic publication and striped process
  locks; expired or unavailable publications retire their referenced summaries.
  A bounded process-local hash cache avoids rereading an unchanged baseline for
  every alternative. It binds device/inode, size, nanosecond modification/change
  times and expected hash; replacement or mutation requires a new hash. A service
  restart starts with no remembered source checks. Summary bytes are still
  checksum-verified on every reuse, and authority is independently rechecked.
- The native private executor can protect idle primary-worker capacity and
  enforce helper CPU/RAM/process limits in delegated cgroup v2 groups. A second
  worker can borrow available capacity after verified quota reductions. The
  example starts with one worker pending disk-contention measurements.

The successful 100,000-person, 2019–2070 comparison proof used one baseline and
one alternative with **one matched seed each**. Its processor reservation was
1 vCPU and 3 GiB, with a separately configured 2 GiB Node allowance. That is a
working starting point, not a measured minimum or optimum. The proof did not
measure peak memory of the entire processing group. Its total duration also
included registration, validation and browser checks, so it is not a pure
aggregation timing.

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

**Refined retention decision, still to implement:** raw output and finished
comparison/chart data must have **independent retention policies**, initially
set to the same or approximately the same period. Equal initial values are a
starting choice, not an invariant: the operator must be able to shorten or
lengthen finished-result retention without changing raw-output retention.
Treat finished data as retained owned results with explicit byte quotas and
expiry, separately from the temporary intermediate-summary LRU.

For example, seven days for raw output and seven days for finished chart data
would be matching initial settings; later changing chart data to 30 days or
three days must leave raw output at seven days. These are design examples, not
new runtime defaults. Existing raw-output retention uses a seven-day window
with dependency holds and notice/grace rules; finished comparisons currently
use the independent 24-hour cache lifetime noted above. A new policy needs a
defined expiry basis and visible per-result deadlines. Account for preparation
delay and experiments whose configurations complete at different times; matching
duration values alone do not guarantee identical deletion dates. Ordinary chart
reads should not silently renew retention.

The existing 8-GiB combined Visualiser cache includes summaries, active work and
publications; it must not silently promise longer retention at arbitrary
experiment counts. The retained-result budget and retirement rules need to be
made consistent with the selected independent policies.

Keeping finished results after raw-data expiry needs an independent verified
provenance record, ownership/licence and revocation checks that do not require
the CSV files, separate handling of automatic raw expiry versus an explicit
delete/revocation, browser-cache invalidation, and backup/restore coverage.
Removing source-existence checks alone would weaken the current protection.
Changed-baseline/new comparisons may still require per-seed summaries or raw
files; keeping one finished comparison cannot recreate every future comparison.
Setting a longer cache lifetime alone does not implement independent retention:
current source-existence checks would still cap chart availability at raw deletion.
Explicit deletion of all results, account removal and access revocation must
continue to apply to retained aggregates, even when automatic raw expiry does not.

### Measuring eviction-led raw reprocessing

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
- Release borrowed capacity when preparation finishes. Keep the protected
  SingleRun slot and general-service budget outside borrowing. Automatic
  background preparation uses the same aggregation capacity as user-requested
  preparation; it does not get another unrestricted pool.

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
- [x] Record the first local summary-replay resource trials. Genuine raw-output
  resource and mixed hosted trials remain required before approving limits.
- [x] Measure known cache-eviction-led raw reprocessing separately from initial/
  unknown misses, invalidation and rejected entries; expose numeric costs to
  the operator and to future genuine-raw capacity reports.
- [x] Portable comparison export/import: download all available finished
  chart data with its metadata, and reopen it through a bounded local-section
  reader; local integrity/permission/stream and compiled round-trip checks pass.
- [x] Run native Chromium/private-service acceptance for the portable download
  and saved-comparison reader against the new candidate build; 16 October
  browser-only rerun passed with RPC evidence and cleanup.
- [ ] Implement independently configurable raw-output and finished-comparison
  retention policies, initially using matching periods, with explicit byte
  quotas, expiry bases, user-visible deadlines and tested deletion semantics.
- [ ] Support shorter or longer finished-result retention independently of raw
  output; implement provenance, permission/revocation, browser invalidation and
  backup/restore checks before allowing chart access after automatic raw expiry.
- [ ] Conditional follow-up: evaluate revocable loans from unused model-growth
  headroom to a disposable second aggregation worker, subject to the performance
  and recall-safety gates above. Keep disabled until both are demonstrated.
- [ ] Measure the full workload with genuine matching seeds, including several
  alternatives; explicitly separate any local capacity-fixture evidence.
- [x] Implement automatic completion-triggered configuration preparation.
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

## Opt-in runtime assembly

`vm/throughput.toml.example` records the candidate assembly; it is not an
approved measured server profile. It leaves 8 vCPUs/30 GiB for normal MultiRun
starts, protects 2 vCPUs/5 GiB for SingleRun and 1 vCPU/3 GiB for the primary
aggregate worker, and withholds 2 GiB of shared model-growth headroom. Pool
totals are therefore 11 vCPUs/40 GiB. Leave the remaining host capacity for
PostgreSQL, private orchestration, public services and the OS. Check the
provider's actual usable RAM: 48 decimal GB is approximately 44.7 GiB.

The growth buffer cannot admit extra ordinary jobs. A running model, or a
resource-specific retry above its original reviewed size, may use it after
normal recovery checks. It is a shared allowance, not a separate maximum
reservation for every simulation. Primary aggregation and SingleRun holds stay
protected during growth. The present user-data SingleRun allocation remains
8 GiB until its separate preparation-exit/memory acceptance is complete: a
5 GiB SingleRun hold does **not** guarantee admission of that older 8 GiB mode
when MultiRun fills its budget. Promote the tested SingleRun setting separately.

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
- One primary worker is enabled in the example. The optional second worker
  requires its full independently available RAM and scratch allowance. Set
  `max_workers = 2` only after measuring a benefit. This runs independent
  preparations concurrently; it does not parallelise a single JavaScript
  calculation.
- The example allocates 2 GiB of scratch per active worker, a 2 GiB persistent
  summary cache, and an 8 GiB total Visualiser cache including both. Publications
  expire after 24 hours. Byte/entry caps, source identity checks and private
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
10 GiB service limit covers up to two 3 GiB helper groups and orchestration;
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
