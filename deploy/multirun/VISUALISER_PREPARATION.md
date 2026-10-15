<!-- (C) Copyright 2026, by Ross Richardson

Agreed automatic Visualiser preparation design and resource-validation plan.
@author ross richardson
-->

# Automatic Visualiser preparation and resource tests

**Decision record: 15 October 2026. Opt-in implementation and targeted native correctness checks complete; capacity and installed-host acceptance pending.**

The agreed scheduling and reuse behaviour is implemented behind explicit operator
settings. Existing deployments retain their fixed allocations and on-demand
preparation until enabled. The implementation checks below distinguish functional
verification from the outstanding resource measurements and host acceptance.

## Current behaviour and evidence

- Results-page **Prepare Visualiser** requests still work. With automatic mode,
  the private dispatcher discovers verified successful configurations at bounded
  60-second intervals, prepares standalone results, and queues each ready
  selected-baseline/alternative pair without waiting for the whole experiment.
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
   per-seed summaries and standalone chart aggregates. Do not wait for the
   whole experiment to finish. Eligibility for processing is not a promise of
   immediate capacity.
2. If the experiment has a selected baseline, prepare its comparison with
   **every alternative** as soon as both configurations' summaries are ready.
   An alternative that finishes first can prepare its summaries immediately;
   only its paired impacts must await the baseline.
3. Without a selected baseline, prepare configuration summaries and standalone
   aggregates, but defer paired impacts until the user chooses a baseline.
4. Do not calculate alternative-versus-alternative comparisons speculatively.
   Once a baseline is selected, all its alternatives should be available
   together in the Visualiser as their preparation completes.

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

This requires new durable CPU-allocation revisions and Docker reconciliation;
the current executor expects the original CPU limit and only implements live
memory/storage recovery. Reserve capacity before increasing a quota, and verify
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
| Ten-seed baseline and alternative | The intended repetition count and matched-seed calculation workload |
| Multiple alternatives sharing one baseline | Reuse, aggregate growth and unchanged comparison calculations |
| One versus two independently limited workers | Useful concurrency versus disk or memory contention |
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
  guard tests. See commands below. Full-size resource trials have not run.
- [ ] Run the local resource trials and record the measured allocation decision.
- [ ] Measure the full workload with genuine matching seeds, including several
  alternatives; explicitly separate any local capacity-fixture evidence.
- [x] Implement automatic completion-triggered configuration preparation.
- [x] Implement durable, private, role-independent per-seed summary reuse and
  invalidation/retention rules.
- [x] Implement selected-baseline comparisons for every ready alternative.
- [x] Implement bounded priority for active-user and automatic preparation before
  new simulations, protected aggregation capacity and borrowing for a second
  worker; align reservations with enforced limits and provide bounded model
  admission opportunities when resources fit. Native enforcement remains to test.
- [x] Implement fair, revocable 1-to-2-vCPU simulation boosts, durable allocation
  reconciliation and suitable minimum-CPU runtime budgets.
- [ ] Measure dynamic transitions and mixed hosted load separately from the
  fixed-quota trials.
- [ ] Test publication, restart and permission behaviour under concurrent work.
- [ ] Repeat representative concurrency and responsiveness measurements on the
  VPS before approving memory limits and maximum worker count for production.

## Prepared laptop harness — 15 October 2026

`visualiser_capacity.py` runs disposable, labelled Docker workers. Each worker
has **one enforced vCPU**, a fixed whole-cgroup memory limit, no swap, no network,
a read-only root and read-only source mounts. Only its own evidence directory is
writable. No simulations, database, emails or production service are launched.
Code is snapshotted before execution; the image ID, code/data hashes and Python,
Node and kernel versions are recorded. Containers are removed after each case.
Bulk intermediates are reclaimed after success or failure; small reports and
samples remain. No Docker-wide cleanup or image pruning is performed.

The initial matrix is 3, 2 and 4 GiB per worker, each with one and two workers.
Two workers execute **two independent complete comparisons**; this measures
concurrent preparation throughput, not parallel seed processing within one
comparison. The current production scheduler still has only one worker. Node's
old-generation heap limit is total memory minus 1 GiB, and the Python helper's
address-space limit is total memory minus 256 MiB. Both run inside the fixed
total cgroup cap. These exact settings are recorded, rather than confusing a
heap limit with total memory.

Stages separately record replay/raw parsing, combination, validation, publication,
indexing, catalogue and a representative selected-chart read. Samples include
charged/working memory, cgroup peak, OOM events, CPU use/throttling, block I/O and
pressure. The controller and its descendants are measured together. The full
HTTP/private-service stack and browser are not running; these chart-helper
timings do **not** establish website latency. Total case throughput includes
container startup, source verification and scratch cleanup; use stage timings
to distinguish those costs. Successful cases must have identical publication,
index, catalogue and view hashes. Failed memory cases retain evidence and the
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
with this provenance; regenerate summaries from raw data to test another
calculation version.

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
  deploy/multirun/visualiser_capacity.py run \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --build /tmp-codex/visualiser-build-20261010-selective-e \
  --fixture /tmp-codex/visualiser-capacity-fixture-20261015 \
  --memory-mib 3072 2048 4096 --workers 1 2 \
  --output "/tmp-codex/visualiser-capacity-$(date +%Y%m%d-%H%M%S)"
```

The two-worker cases require at least **8 GiB free disk** and available RAM equal
to the workers' combined caps plus **2 GiB host headroom**; the 4 GiB/two-worker
case therefore requires 10 GiB available RAM. These checks run before each case.
They are test admission margins, not calibrated production reservations. At
preparation time the laptop had about 17 GiB free disk. The sequence uses the
same exclusive lock as the research/SingleRun benchmarks when their output
parent is `/tmp-codex`; do not run other heavy work alongside it.

Default case timeout is three hours; `--timeout-seconds` can set 60–43,200 seconds.
Use `--memory-mib 3072 --workers 1` for a first isolated case. `--rounds 2` repeats
the matrix; a second sequence can specify `--memory-mib 4096 2048 3072` to change
order. File caches are not cleared. Checksumming and previous cases warm caches,
so interpret logical bytes and measured physical block I/O separately. Two
workers share the same fixture inputs, which can favour cached reads.

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
  --build /tmp-codex/visualiser-build-20261010-selective-e \
  --output /tmp-codex/visualiser-native-fixture-NEW
```

Those two trial paths are placeholders, not currently available data. The
sources must contain verified 100,000-person outputs through 2070 with the
requested actual repetition count. Then use the normal `run` command with this
new fixture. A one-seed native pair is also supported with `--repetitions 1` but
does not certify the ten-seed workload. Full genuine ten-seed native acceptance
remains to do, preferably where sufficient retained-output storage is available.

### Known multi-alternative publication bound

The retained baseline has 135,095 summary cells and the alternative 135,017.
A replay with two alternatives therefore produces approximately **405,129**
aggregate rows, exceeding the existing **400,000-row** combined-publication cap.
`--alternatives 2` or `3` can create stress fixtures. The original capacity
harness still exercises a monolithic publication and can hit this bound; it
does not silently raise production limits. The new service composition builds
separate bounded pairs and merges only catalogues/selected chart sections.

- [x] Replace the requirement for one ever-larger combined publication with
  bounded per-configuration/per-pair results and selected-chart delivery as part
  of the reusable-summary design. Fictional tests exceed the combined row cap
  while validating independent pairs and selected-section parity.
- [ ] Rerun genuine multi-alternative service and resource acceptance.

### Validation of the harness

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex PYTHONPATH="$HOME/git/JAS-mine/JAS-mine-web" \
~/simpaths-browser-tests/venv/bin/python -m unittest \
  deploy.multirun.test_visualiser_capacity_fixture \
  deploy.multirun.test_visualiser_capacity \
  deploy.multirun.test_visualiser_capacity_worker -v
```

Guard tests use tiny fixtures and mock Docker/cgroup state. A tiny 52-year
fixture has also exercised the actual pinned calculation and publication,
index, catalogue and view helpers. Neither check is whole-cgroup capacity
evidence. Native Docker resource trials require the user's terminal because
the development sandbox cannot access the Docker daemon.

Preparation checks on 15 October: **36 harness tests passed**, the retained
fixture's full source/build hashes matched, and the Docker SDK accepted the
constructed fixed-limit/isolation settings without contacting the daemon.

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
