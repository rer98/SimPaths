<!-- (C) Copyright 2026, by Ross Richardson

Hosted SimPaths Online capacity and responsiveness test plan.

@author ross richardson
-->

# Hosted load-test plan

Prepared 11 October; reviewed 20 October 2026. Status: hosted plan, not completed
hosted acceptance. The intended trial is Contabo VPS 12 EU, with funding agreed;
provider/setup checks and a measured installed-host configuration remain open.
Use this with the [deployment checklist](DEPLOYMENT_CHECKLIST.md),
[research measurements](multirun/RESEARCH_STORAGE.md) and
[operator metrics guidance](multirun/OPERATOR.md).

## Purpose and workload

Determine whether the selected Linux host can provide acceptable simulation
throughput and website responsiveness for four signed-in researchers. Assess a
shared-processor host first if that is the approved purchase; move to dedicated
processors or greater capacity when measured results justify it.

Matteo's planning target is 100,000 initial people, 2019–2070 inclusive (52 annual
years), up to ten sequential seed repetitions per parameter configuration and
four concurrent users. Confirm the repetition limit and representative policy
settings with the team before opening the service.

Four users do not require four simultaneous model processes. Users can configure
experiments, inspect results and queue work while the admission controller admits
only configurations whose CPU, memory and storage allowances fit. Each admitted
configuration runs its repetitions sequentially; baseline and alternatives are
separate configurations. An interactive SingleRun also consumes shared capacity.

Use public permitted training inputs and separate test accounts. Record the
exact application commits, model release/JAR/image digests, prepared-input
identity, Visualiser build, collector settings and frozen resource/runtime
policies. Match seeds across baseline and alternatives. Do not use restricted
provider inputs for capacity testing.

## Provider delivery checks

For the first Contabo trial and any later provider comparison, retain a dated
copy of the purchased specification, order/plan identifier, region and relevant
written clarifications about shared/dedicated CPU allocation, sustained CPU use,
storage and network allowances. Record explicit guarantees separately from
advertised maxima and general performance descriptions. Use the actual order as
the reference when a supplier's public plans subsequently change.

### Inventory and bounded component checks

Before application trials, record the host identity, UTC time, kernel, tool
versions and the following observations. Repeat inventory after a resize,
migration or replacement, retaining the earlier record.

| Area | Initial check | Performance evidence |
| --- | --- | --- |
| CPU | Guest-visible vCPU count, advertised processor identity and guest/service/container CPU restrictions | Fixed-work scientific timings and CPU consumption at known quotas; sustained concurrent throughput; reported steal time and pressure where exposed |
| RAM | Guest-visible memory in bytes, OS overhead, available memory and swap configuration | Working-memory/heap observations, swap or OOM events and stability within the admitted service budget |
| Storage | Virtual-device, partition and filesystem capacities in bytes, allocated/free space and the filesystem used by model/Docker data | Bounded reads/writes on the intended filesystem; latency/throughput, I/O pressure and real Visualiser processing times |
| Network | Purchased port/traffic terms, selected region, external test route and endpoint identity | Repeated external latency, packet loss and bounded transfer rates in both directions where a suitable endpoint is available |
| Reliability | Boot identity, service start times and available host/service event records | Failed requests, unexpected restarts and performance degradation across the trial; correlate with application and resource events |

Keep decimal GB, binary GiB, network bits and transferred bytes explicit. A
filesystem smaller than the virtual disk may indicate partitioning or filesystem
overhead; compare each layer before treating it as missing purchased capacity.

Run component benchmarks before user work or in a separate declared test window.
Record explicit duration, temporary-file and network-transfer bounds chosen to
fit actual headroom. Disk probes use unique disposable files on the intended
filesystem, leaving the operator reserve available. Record direct versus buffered
I/O and cache conditions; verify probe completion and cleanup. Network probes
use an authorised endpoint with enough capacity; record its possible limits and
the route. The 15-second collector does not itself perform these disk/network
benchmarks or measure complete external browser transfer/paint time.

### Repeated application evidence and interpretation

Enable external and new-launch Java review at **15 seconds** for the initial VM
trial, verifying acknowledgement and actual launch mode. Repeat the same pinned
single-configuration reference, selected concurrency case and aggregation workload
in **at least three separated time windows**, preferably at different times of
day. Preserve inputs, scientific settings, quotas, monitor modes and artifact
identities; record other activity, cache conditions and any host change. Report
all outcomes, medians and ranges, including slow or failed trials. The laptop
results provide a workload reference, not a guaranteed VPS completion time.

Distinguish Docker/cgroup throttling from host contention: an intentionally
limited model can accumulate throttled time while using its CPU allowance
normally. Reported steal time can support a contention diagnosis; low, absent or
zero steal time does not exclude limitations hidden by the hypervisor. Disk,
network and application bottlenecks can also reduce throughput. Guest processor
and virtual-disk identities do not certify exclusive physical cores, underlying
SSD/NVMe media or the provider's oversubscription ratio.

Classify the outcome separately as **capacity verified/mismatch/unknown** and
**service targets met/not met/inconclusive**. An observed slowdown is not by itself
proof that an explicit provider guarantee was breached. Retain the ordered versus
observed capacities, applicable promised metric, test bounds, UTC windows,
artifact/host identities, full trial distribution, missing counters and cleanup
evidence. Use this record for a specific supplier query, tuning decision or
repeatable comparison with another provider.

## Capacity budget before launching work

- Record actual guest RAM, vCPU count, CPU model, swap policy, filesystem and
  quota support, disk size/free space, and hosting plan/location. Distinguish GB
  from GiB; use actual guest measurements rather than a plan's advertised size.
- Use the current tested large-workload candidate of a 4 GiB maximum heap and
  fixed 5 GiB container for the first matched model tests. All A/B/C cases and
  quiet repeats passed; the earlier single C timing does not establish a benefit
  from more RAM. The six population/horizon profiles also passed at 4/5 GiB.
  The six smaller-memory cases also passed two repetitions per JVM at fixed
  2 vCPUs on 20 October; see [MEMORY_TRIALS.md](multirun/MEMORY_TRIALS.md).
  Selected profiles still need lower-CPU, ten-repetition and hosted qualification.
  Keep operational expansion and resource-specific retries available as safeguards
  in the service, within explicit ceilings.
  Check representative population/horizon and collector combinations when
  defining profile coverage, including shorter common workloads and the research
  boundary. A successful baseline is not evidence for every policy or collector
  combination; avoid untested interpolation or an automatic smaller fallback.
- Start from the opt-in [throughput candidate](multirun/vm/throughput.toml.example),
  after measuring host capacity: an 11-vCPU/39.5-GiB common pool containing
  8 vCPUs/30 GiB for ordinary models, protected 2 vCPUs/5 GiB for SingleRun,
  protected 1 vCPU/2.5 GiB for primary Visualiser preparation and 2 GiB shared
  model-growth headroom. Keep OS/PostgreSQL/public/private orchestration outside
  the pool. If 48 GB is decimal, the nominal total is about 44.7 GiB; confirm
  actual guest memory. These are candidate budgets, not accepted host sizing.
- Model CPU floors are 1 vCPU with revocable boosts up to 2 vCPUs and fairness
  between users. Reclaim boosts before admitting otherwise feasible work, while
  preserving running-job floors. Completed model reservations can transfer into
  compact preparation or a fully admitted 5-GiB/2-vCPU seed-parallel group. Do not
  count the old model and its transferred allocation twice. Include helper
  scratch, the executor's manager limit and retained files in physical budgets.
- Use CPU limits consistently across matched trials. More advertised vCPUs do
  not establish useful model throughput; measure it under sustained load.
- The research storage candidate is 4 GiB fixed plus 4 GiB per repetition:
  12 GiB for two repetitions and 44 GiB for ten. The shorter 50,000-person/2026
  standard of 4 GiB plus 256 MiB per repetition does not cover this workload.
- Two measured repetitions retained 6.93 GiB. Linear extrapolation gives about
  34.65 GiB per ten-repetition configuration, 69.3 GiB for a baseline/alternative
  pair, or 277.2 GiB for four retained pairs. This excludes input packages,
  database, images, active scratch, aggregates, ZIP cache and free-space reserve.
  A nominal 320 GB disk is about 298 GiB before filesystem overhead: do not
  promise four such retained experiments on it without a complete disk budget.
- Include already retained results when checking space for the next job. Quota
  allowances and admission reservations differ from currently occupied bytes.
  Admission must still protect physical free space and operator headroom.
  For each filesystem, budget retained results outside active workspaces plus
  full active workspace allowances, shared inputs/images/database, caches and
  the operator reserve. Active workspace allowances already include their own
  growing output: do not add that output again. Separate filesystems need separate
  budgets. Distinguish logical file bytes, allocated blocks and actual free space.

Use tested starting allowances for admission; do not admit extra work against
sampled usage alone. Spare capacity for unexpected live growth is opportunistic,
not a reservation of every configuration's full growth ceiling at launch. If
growth cannot fit, preserve the documented bounded-failure/retry behaviour and
notify the owner/operator rather than silently overcommitting the host.

## Test sequence

Run these stages sequentially. Increase concurrency only after the preceding
stage passes and the configured allowances fit. Keep authentication, ownership,
quota and resource ceilings enabled throughout.

| Stage | Work | Evidence and decision |
| --- | --- | --- |
| 1. Installed-host acceptance | Purchased-versus-observed provider inventory and bounded component checks; public HTTPS, sign-in mail, restricted database role, native quota broker, restart/boot behaviour and operator metrics | Complete the installation-specific checklist and provider delivery record; establish quiet-host CPU/RAM/disk/network and HTTP baseline |
| 2. Single configuration | Two seeds, 100,000 people through 2070, same pinned model/settings as laptop cases | Verify annual output, seed/settings identity, runtime, GC, working/total memory, storage peak, polling and settled cleanup |
| 3. Concurrent configurations | Increase admitted work in stages (for example two, four, then the largest feasible candidate count), retaining a one-configuration reference and protected service budgets | Compare throughput, completion spread, CPU boosts/reclamation and storage admission. The 30-GiB model budget can fund at most six 5-GiB starts before donated preparation or other contention; actual CPU/disk/readiness may admit fewer. Do not force a test that cannot fit |
| 4. Four-user workflow | Four owners submit work, poll status and use Results; allow excess configurations to queue | Measure waiting time and fairness; confirm owner isolation and admission limits; report how many jobs actually run |
| 5. Mixed activity | While models run, exercise automatic stopped-model summary handover, ready baseline/alternative pairs, cached/regenerated charts, an authorised ZIP and one interactive session within its protected allowance | Measure first usable charts, all required charts, queue fairness, boost reclamation and website/SingleRun responsiveness. Compare one/two admitted preparation groups and seed-parallel fallback under actual disk contention |
| 6. Ten repetitions | At least one full ten-seed configuration, then a matching alternative if disk budget permits | Measure actual output growth, runtime allowance, memory drift, cleanup and aggregation; projections become observations only after completion |
| 7. Repeat and soak | Repeat matched concurrency trials in at least three separated time windows; include a sustained period with retained output present | Assess shared-host variability, queue drainage, trends and background maintenance under realistic use |

Early concurrency tests can use two repetitions per configuration to limit time
and disk demand. That does not certify ten-repetition concurrent workloads.
Do not run four baseline/alternative ten-seed pairs together merely to fill a
server; stage them only when the complete storage budget permits it.

For timing comparisons, keep software, input bytes, seeds, collector settings,
CPU allowance, logging/sampling and background activity constant. Record warm
versus cold filesystem caches, reboot/host changes and other workloads. Bracket
concurrency trials with single-configuration references and alternate trial order
where practical. Report every trial, median and range; one A/B/C observation
per setting is a screening result, not a reliable causal speed estimate. Repeat
close or inconsistent results before selecting a profile. A one-configuration
reference should include the same aggregation/background workload when assessing
the effect of model concurrency alone.

For scientific memory/heap comparisons, disable live expansion and automatic
resource retries in the isolated test policy so changes cannot hide inadequate
starting allocations. Run a separate bounded safeguard test afterwards with
growth/recovery enabled; distinguish successful first-attempt completion from
completion rescued by growth or retry. Recovery mechanics already have local
proofs; selected-host enforcement and capacity remain to verify.

## Measurements

Select the existing operational collector and optional review controls explicitly;
record which are enabled and the 15-second review interval. External review is
live-switchable; detailed Java applies only to newly launched processes. Record
monitoring-off/on qualification separately, and keep expensive diagnostics or
other benchmarks out of matched trials. Collect monotonic durations and record unavailable samples rather
than treating them as zero. Keep diagnostic evidence private and credentials,
cookies, personal information and raw scientific rows out of shared reports.

Operational summaries provide counts, means, maxima and sampled 80%/90% durations;
their hourly data cannot reconstruct median/p95/p99. Optional performance traces
also retain bounded response histograms, whose percentile results are bucket
upper bounds. Collect separate bounded per-request timing samples in the test client, with
route category, response class, sample count and the chosen percentile method.
Measure server processing and client transfer/paint time separately. Optional
review now records available CPU/steal, I/O/pressure, service/PostgreSQL and
Java progress/heap/GC observations, with coverage and missing counters labelled.
GC counters are not pause-time measurements; sampled peaks are not exact maxima.
Installed permissions, kernel support and disabled database I/O timing can leave
fields unknown. Supplement with bounded host/lease diagnostics where needed;
none of these metrics is guaranteed by the basic operational-summary command.

| Area | Record |
| --- | --- |
| Host | CPU utilisation and hypervisor steal time; available RAM, swap activity, disk free space and I/O pressure; sampled time at or above 80% and 90% of applicable capacity |
| Model | Allocated CPU/heap/container/storage ceilings; actual heap, GC counter deltas, working memory and total cgroup memory including cache; workspace bytes/native quota readings; time at pressure thresholds |
| Queue | Submission/admission/start/finish times, waiting time by owner, active reservations, attempts, growth events and failure categories; no duplicate execution or premature release |
| Web | Status/control response median, p95, p99 and maximum, response-header and whole-response durations, error counts, worker heartbeat/lease failures |
| Results | Aggregation queue/processing time, first chart readiness, catalogue/chart bytes and latency, refresh cache reuse, download preparation and full-transfer duration/checksum |
| Output | All 52 years for each seed, per-file sizes/hashes, original inputs/settings/seed sequence, retained bytes, scratch reclamation and confirmed model termination |

GC counters are not a direct measurement of wall-clock time recoverable by a
larger heap. Keep model execution, container lifetime, output verification and
whole rehearsal durations separate. Compare same-seed hashes explicitly; record
the known BenefitUnit.csv reproducibility differences rather than requiring or
claiming exact equality for that file. A BenefitUnit mismatch is still a visible
unresolved reproducibility warning, not a blanket exemption for incorrect data;
record the exact files, sizes and hashes and investigate new patterns. Unexpected
mismatches in other annual CSVs prevent claiming equivalent scientific output.
Operational filenames, options and timestamps may differ without representing
a scientific difference; align comparisons by seed and annual CSV name.

## Provisional acceptance and stop criteria

Agree these starting targets with the SimPaths team; they are test criteria,
not promises of production completion times or final service-level commitments.

- No lost jobs, duplicate seeds, access leaks, unexpected model failures,
  lease-expiry errors or premature capacity release. Verify termination and
  cleanup before counting resources as available.
- Small status/control requests: p95 at most 1 second and p99 at most 2 seconds
  during sustained load, with no unexpected 5xx responses. Separate expected
  permission denials from availability errors and large-file transfer time.
  Report sample counts and successful/error distributions; too few observations
  cannot establish a dependable tail percentile. Client timeouts count as errors,
  not missing samples that improve the latency summary.
- Chart actions: aim for p95 at most 2 seconds after prepared results are ready
  on a recorded test connection; measure first load, uncached navigation and
  authorised cache reuse separately. Aggregation itself has a separate queue
  and preparation duration, not a two-second promise.
- All admitted jobs finish within their agreed frozen runtime budgets. Establish
  an acceptable user waiting/completion-time budget before production selection;
  do not inherit the short-horizon one-hour-per-repetition policy blindly.
- No host OOM, persistent swapping, exhausted physical disk or unreliable quota
  enforcement. Investigate sustained 80%/90% pressure against service headroom;
  model CPU saturation alone can be normal during productive calculation.
- Stop admitting new test work if measured headroom becomes inadequate, errors
  occur or cleanup is uncertain. Preserve diagnostics; allow existing work to
  settle when safe. If continuing risks host failure or data loss, use supported
  cancellation/shutdown controls and verify termination before releasing capacity.
  Do not delete active files or bypass the service's accounting to recover space.

## Interpreting shared versus dedicated processors

Compare configurations completed per hour, individual model duration, queue
waiting time and browser latency across repeated trials. Record virtualisation
steal time alongside CPU utilisation and I/O/memory pressure where available.
A slow HTTP request alone does not prove noisy neighbours or shared-CPU
contention: aggregation, disk I/O, GC, network transfer or application blocking
can produce similar symptoms. Low reported steal time also does not prove that
there is no shared-host scheduling limitation.

Keep the shared host if the repeated sustained tests meet the agreed user
experience and capacity targets. Reduce concurrency if extra parallelism harms
throughput or responsiveness. Consider dedicated processors when unexplained
runtime variability or sustained CPU contention remains material after checking
application, memory and I/O causes; increase RAM/storage when those are the
measured constraints. Repeat capacity/performance checks on a replacement host;
functional and privacy proofs remain useful but timings do not transfer unchanged.

## Report and handover record

Create a dated report for each stage containing exact release/host identities,
configuration and user counts, allocations, test start/end times, collector
coverage, latency distributions, resource peaks/threshold durations, job timings,
failures, attempts, output audit and cleanup result. Summarise repeated trials
without discarding slow ones. Record missing readings and departures from plan.

Conclude with supported workload bounds, recommended admission concurrency,
starting profiles and safeguard ceilings, retention/cache quotas, remaining
uncertainty, estimated capacity for four users and reasons for keeping/upgrading
the host. Preserve compact statistics/hashes before authorised disposal of bulk
test output. Finalise production policies only after reviewing these results.
