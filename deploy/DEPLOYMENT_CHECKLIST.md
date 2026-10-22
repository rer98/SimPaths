<!-- (C) Copyright 2026, by Ross Richardson

SimPaths Online deployment readiness, verified local evidence and installation order.

@author ross richardson
-->

# SimPaths Online deployment readiness

**Reviewed:** 22 October 2026. **Deployment status:** Ross supplied the provisioning
notice for a Contabo Cloud VPS 12 (2026), Hub Europe, on 21 October. Account creation
and the initial order have therefore progressed to a provisioned VPS. Matteo
confirmed funding on 14 October through Alliance for Microsimulation and Policy
Analysis CIC and requested that project accounts refer to that entity. The first
root SSH inventory succeeded on 21 October: Ubuntu 24.04.5 LTS, 12 guest vCPUs,
50,514,526,208 bytes of RAM (47.045 GiB), a 429,496,729,600-byte virtual disk
(400 GiB), an ext4 root filesystem and cgroup v2. Dedicated-key root SSH access
and a fresh separate administrator login with password-authenticated sudo were
verified on 22 October. The initial key-only SSH restrictions and IPv4/IPv6 UFW
rules passed a separate fresh IPv4 administrator connection and effective-policy
checks; the temporary rollback timer was cancelled. The initial Ubuntu update
review found no eligible upgrades and three phased updates. Initial SSH/firewall
settings and administrator/sudo access also passed after a verified reboot.
External IPv6 checks, quota-compatible storage, final billing/administration
arrangements, domain and provider CPU-use checks remain to verify. These initial
checks are not installed-host acceptance; keep addresses, credentials and detailed
host evidence outside this public document.

Use this checklist to prepare and validate the first SimPaths Online installation,
covering SingleRun, UK MultiRun and the Policy Impact Visualiser connection.
The evidence table records passing local tests. The unchecked items require the
actual deployment configuration, host or external service. Older dated plans
retain the development history; this checklist supplies the current deployment order.

The first VM deployment uses the selected, currently released and qualified
artifacts. Unpublished dependency, library or model fixes remain tracked
follow-up work and do not postpone installation while waiting for publication.
Record the deployed versions and open findings, perform the installed-host
acceptance checks, and use the [maintainer update workflow](MAINTAINER_GUIDE.md#updates-after-the-first-vm-deployment)
for subsequent releases. Review available updates according to their impact;
neither this first installation nor a later version freeze ends maintenance.

### Email provider and administrator inbox

The [email setup guide](EMAIL_SETUP.md) records the agreed initial Brevo Free
account, dedicated `SimPaths Online` SMTP key, sending/admin address and separate
notification/export/backup controls. The populated private configuration is saved
outside Git; required settings and local permissions have been checked. SMTP
authentication, sender/domain status, real inbox/attachment delivery and VM
configuration remain installation acceptance tasks. No SMTP key is stored here.

### Backup arrangement for the initial trial

Ross agreed on 21 October to omit Contabo Auto Backup from the initial order.
Use the application's encrypted backups with a separate provider; the destination
has not yet been selected or tested. Keep deployment configuration, secrets and
required software/images separately recoverable. Configure and test remote backup
and independent restoration before accepting real users' data. The included
snapshots can provide additional rollback points. Auto Backup remains an optional
later addition. See the [backup plan](multirun/BACKUP.md#initial-deployment-backup-decision).

## Current implementation and next work

The following capabilities are implemented and have local/native fixture evidence;
their installation-specific acceptance is still open:

- Separate public gateways and private executors for MultiRun and SingleRun,
  with private ownership/permission checks and independent public restarts.
- Fair adaptive simulation CPU quotas, baseline-first admission, immediate
  completion discovery, [direct resource handover](multirun/VISUALISER_HANDOVER.md)
  and parallel per-seed summary preparation. Completed configurations continue
  through ready comparisons/publications before releasing their preparation
  allocation; missing partner summaries do not retain idle resources.
- Incremental experiment additions, [portable finished-chart downloads](multirun/VISUALISER_PREPARATION.md),
  independently configured raw/summary/chart retention and chart access after
  routine raw expiry. Explicit deletion and revocation still deny derived access.
- [Visualiser storage pressure](multirun/VISUALISER_STORAGE.md): 72-hour-unused
  finished-result eviction, durable suppression of automatic recreation, protected
  readers, administrator warning levels and live approval of bounded storage growth.
- Intermediate-summary and finished-chart backup/restore, including encrypted
  recovery with [pending/adopted preparation transfers](multirun/HANDOVER_RECOVERY.md).
- Storage-aware queue admission, administrator incidents, optional one-off owner
  email after six hours of persistent storage delay and a subsequent start notice.
- [Live scheduler storage budgets](multirun/STORAGE_BUDGET.md), with filesystem
  growth tracking, reserved headroom, optional ceilings, fixed increases and
  restart/backup preservation. The 124 passing native cases and corrected
  seven-case rerun qualify the fixture scope; mounted-host growth remains open.
- [Optional performance review](multirun/PERFORMANCE_REVIEW.md), with live external
  controls, launch-selected detailed Java observations, bounded timelines and
  workload/pipeline reports. Optional review defaults remain off; safety checks
  and basic lifecycle records remain enabled independently.
- SingleRun public-input preparation exits before Build. The tested user-data
  catalogue now requests a 4 GiB application heap and 5 GiB container, with a
  4 GiB preparation-worker heap used in the preceding, non-overlapping phase.

These mechanisms do not establish the chosen shared host's throughput or
responsiveness. Preserve exact tested artifact identities; the older image/browser
and update reports below are not blanket acceptance of today's source tree.

### Work order from this review

1. Preserve and review the completed [smaller-memory, same-JVM campaign](multirun/MEMORY_TRIALS.md):
   all six cases passed both repetitions at 2 vCPUs on 20 October. The genuine
   nine-job quiet monitoring comparison and both fictional quiet campaigns passed
   too. The initial memory/monitoring campaigns are complete. Select workload-specific
   candidates for lower-CPU/hosted qualification; the 4 GiB heap / 5 GiB allowance
   remains available as the broader tested reference. Precise monitoring overhead,
   ten-repetition stability and chosen-host mixed load remain unqualified. The
   long Gini-on attempt reached 2069 before its runtime deadline; its retained
   observations are sufficient for preliminary planning. Another quiet laptop
   rerun is optional. Defer full-horizon Gini-on completion, final output checks
   and repeated-JVM acceptance to the VPS, with a separately reviewed longer
   runtime budget. See the [Gini planning decision](multirun/OUTPUT_TRIALS.md#planning-value-and-whether-to-repeat-the-quiet-run);
   the incomplete campaign remains failed.
2. Complete the remaining [dependency release gates](../../solveit_SimPathsWeb/SECURITY_DEPENDENCY_TRIAGE_2026-10-15.md#runtime-exclusion-and-smaller-compiler--20-october-2026).
   Isolated Python/installer compatibility, patched image qualification, guarded
   Pebble exclusion, exact-ID scans and the smaller Visualiser compiler's
   emitted-package/browser acceptance are complete. The four derived images have
   no high/critical findings and each retains 55 medium/low Ubuntu entries with
   no fix recorded. The [21 October applicability review](../../solveit_SimPathsWeb/security-review/2026-10-21/UBUNTU_APPLICABILITY.md)
   records every entry: five advisory IDs have absent prerequisites in the
   reviewed setup, nine native-library paths remain open, and thirty have no
   matching exposed path found. The latter does not establish safety or a fix.
   Exact-image native inspection passed for all four images: matching package
   versions, ELF64 headers, selected utilities, local Xvfb sockets and cleanup
   are verified. Installed-host applicability and the vendor security assessment
   of Java's bundled native code remain separate review items. The
   [offline native/JRE inventory](../../solveit_SimPathsWeb/security-review/2026-10-21/NATIVE_LIBRARIES.md)
   now verifies all 33 saved layers and the selected bundled declarations. It
   confirms Temurin 25.0.4+7. Record the selected version and track the newer
   Java 25 security baseline as follow-up work; qualify a separate pinned
   replacement when available before activating that later release. Unpublished
   vendor updates do not delay today's VM deployment. Historical OS/Maven scan
   counts do not cover the complete JRE.
   The runtime metadata checker is prepared; today's two vendor API responses
   agree on 25.0.4.1, below the reviewed 25.0.5 floor. Its plan remains
   `waiting_vendor_release` for the optional future refresh; it does not block
   installation of the current qualified release or approve a replacement.
   The [browser asset integrity review](../../solveit_SimPathsWeb/security-review/2026-10-20/BROWSER_ASSETS.md)
   now verifies all 12 remote script/style responses and five local vendor files;
   the screenshot loader's missing SRI is fixed and three Chromium acceptance
   cases pass. Provider-managed Turnstile and referenced fonts remain documented
   exceptions. The [refreshed MultiRun release](multirun/RELEASES.md#refreshed-security-candidate--20-october-2026)
   passed native preparation/queue execution on 20 October. Perform further
   installation/build/scan work outside quiet benchmarks; preserve the older
   scientific references.
   The 21 October certificate-store follow-up also passed: certifi 2026.10.19 /
   OAuthLib 4.0.0 / pip 26.2.2 completed 59 database/HTTP cases, 123 native checks,
   both compiled browser proofs and the SingleRun HTTPS/restart workflow. Trivy
   matched all 56 resolved packages with no findings using that day's database.
   The shared certifi pin now selects the tested version; fresh installed-host
   verification remains due. See the
   [certificate-store qualification](../../solveit_SimPathsWeb/security-review/2026-10-21/CERTIFI_UPDATE.md).
3. Freeze a new release inventory and review its migration/rollback procedure.
   There are now **32 batch migrations**, including handover (030), detailed
   performance history (031) and administrator monitoring exports (032). The retained 22-migration update pair is historical;
   it does not qualify code-only rollback for the current release.
   A separately frozen 32-migration pair passed 55 regressions, all seven native
   stages, final integrity and cleanup on 20 October. Carry its exact selected
   commits and the separate later browser-asset qualification into the inventory;
   do not relabel an older report as acceptance of an unspecified new tree.
4. Complete provider checks, provision private staging and execute the installed
   host, external privacy, recovery and mixed-load checks below. Funding is
   already agreed; provider suitability, billing/account setup and technical
   handover arrangements still need recording.

### Candidate VPS 12 resource budget

The opt-in [throughput configuration](multirun/vm/throughput.toml.example) records
this starting candidate, not a measured hosted optimum:

| Purpose | CPU allowance | RAM allowance |
| --- | ---: | ---: |
| Ordinary MultiRun work | 8 vCPUs | 30 GiB |
| Protected interactive SingleRun capacity | 2 vCPUs | 5 GiB |
| Protected primary Visualiser preparation | 1 vCPU | 2.5 GiB |
| Shared unexpected-model-growth reserve | Within admitted CPU budgets | 2 GiB |
| Total common pool | 11 vCPUs | 39.5 GiB |

Keep the remaining actual host capacity for the OS, PostgreSQL, public/private
orchestration and other service work. If the provider's 48 GB is decimal, it is
about 44.7 GiB before guest/system effects; measure actual available memory.
The CPU/RAM allowances are admission budgets, not a guarantee of dedicated cores.
Do not infer final concurrency from advertised specifications alone.

The first guest inventory reports 47.045 GiB total RAM, leaving approximately
7.545 GiB outside this 39.5 GiB common pool for system and shared services. It
reports no swap. The complete 400 GiB virtual disk is already partitioned, mainly
as the ext4 root filesystem; inspect and arrange the required XFS storage before
installing model workspace quotas or SingleRun writable-layer limits. The guest
CPU count and generic AMD EPYC identity do not establish sustained throughput or
exclusive access to physical cores.

The candidate retains one protected preparation allowance and permits two
independently admitted groups. A group can use two seed tasks within a total
5 GiB / 2-vCPU allowance, or fall back to a compact sequential group when that
larger allowance cannot fit. Handover transfers reservations after verified
termination; it does not double-count the stopped model and its preparation.
Install and verify the delegated resource drop-in and whole-group limits before
enabling it. Lending emergency growth RAM remains unimplemented and disabled.

## Earlier local foundations

The dated observations below retain earlier build evidence. Current preparation,
retention, resource and release decisions are summarised above; later evidence
in the table supplements these historical measurements.

The [9 October Visualiser update](multirun/VISUALISER.md) merges Reese's current
multi-scenario charts and seed-paired calculations into both proposed PR branches.
The candidate bundle shows all selected alternatives together and keeps names and
identities in chart exports. Its 103 local source, packaging, native-CSV and
publication/helper checks pass. All 91 focused database checks passed in
`vm-visualiser-paired-20261009-183331`; after correcting an ambiguous test locator,
all nine browser stages passed in `vm-visualiser-paired-20261009-184750`. The
passing browser run records no uncaught errors and confirms database cleanup.
Earlier browser reports below cover previous bundles.
Restricted-provider aggregate release rules still require separate approval.
Two one-seed 100,000-person/2070 simulations and their retained-output browser
comparison have passed locally. A two-seed baseline also passed on 11 October,
with live memory growth and verified output/cleanup. Ten-seed capacity and hosted
concurrency still require calibration. Fixed-memory Cases A/B/C have now also
passed; [their comparison](multirun/RESEARCH_STORAGE.md#abc-comparison-record)
records the feasible sizes and single-observation timings.

The [10 October selected-chart connection](multirun/VISUALISER.md) loads a small
catalogue and only the displayed variable/breakdown/scenario sections, with
bounded per-page caching and access checks. All 53 native PostgreSQL/helper cases
and ten Chromium stages pass for the `selective-e` bundle, with no uncaught
browser errors and confirmed cleanup. The full-size retained 100k/52-year
selective Chromium check also passes: initial catalogue/chart requests total
244.4 KiB rather than the previous 124.1 MiB complete response, and all captured
aggregate responses total 606.2 KiB. All three native CSV exports match the
earlier full-data exports byte-for-byte; original outputs, permissions and
cleanup are verified without model reruns.
Server publication/configuration/storage limits remain; index files count toward
cache capacity, and hosted concurrency still needs calibration.

The [11 October browser reuse/hint update](multirun/VISUALISER.md) adds
session-partitioned IndexedDB sections, fresh access checks before reuse,
global byte/entry and fixed-age limits, sign-out invalidation across tabs and
bounded pointer/keyboard prefetch. All 11 native cache stages pass with no browser
errors and confirmed cleanup; 19 loader, 15 maintained chart/hook, 11 Results,
two in-process route/scope cases and the compiled application also pass.
The 25 native database cases and all 12 complete service/browser stages also pass
against `cache-b`, with no browser errors and confirmed cleanup. Refresh adds
zero chart transfers while making fresh access checks; real website sign-out
clears both open chart tabs. Existing scientific values, public workload bounds
and HTTP `no-store` responses remain unchanged.

The reports test different recorded source revisions and images. They do not
collectively certify an unspecified future build. Record the exact selected release
and run the relevant checks against it when installing or changing the service.

Optional [background operational history](multirun/OPERATOR.md#focused-proof)
and [tested workload starting profiles](multirun/RELEASES.md) are implemented.
Their native 154-case proof passes without skips, including scheduler isolation,
numeric-only history, frozen profiles, legacy releases, runtime shutdown and
inactive backup/restore preservation. Scientific starting allocations remain
subject to calibration. Fixed-memory Cases A/B/C have passed; repeat timings and
hosted tests remain pending.

MultiRun also has operator-enabled [automatic resource recovery](multirun/VM.md#automatic-resource-recovery):
bounded live workspace/container-RAM growth and resource-specific retries preserve
the original model, inputs, seeds and attempt/time budgets. New reviews freeze the
operator's ceilings; existing work keeps its original policy. Real local Docker/JVM
and XFS fixtures verify recovery mechanics, including lost replies and restarts.
Initial scientific heap/RAM settings remain unchanged, and the growth headroom must
be calibrated on the selected host before enabling the option for users.
The [real-model resource rehearsal](multirun/VM.md#real-model-resource-calibration-and-recovery-rehearsal)
now records full 50,000-person trials with 3/4 GiB heaps in 5 GiB containers,
confirmed Java heap exhaustion followed by a larger-heap retry, and a passing
same-process live RAM/application-storage growth run. Service defaults are
unchanged. The subsequent native XFS run also passed with the real scientific
workload, kernel readback and confirmed cleanup. These local tests complement
the small exhaustion/restart fixtures; selected-host acceptance is still required.

The subsequent MultiRun XFS quota implementation is described in
[WORKSPACE_QUOTAS.md](multirun/WORKSPACE_QUOTAS.md). Local ABI, launcher, journal,
executor, configuration and file-restore checks pass. Its native loop-filesystem
rehearsal passed in `workspace-quota-20261005-011818`; the earlier evidence below
does not certify this new boundary.
The first two native attempts passed 74 regressions and confirmed cleanup, then
stopped at the probe's incorrect `EDQUOT` expectation before launching models.
XFS returned `ENOSPC` at 16 MiB. The corrected probe verifies kernel accounting,
free capacity and a separate successful allocation; all 71 local checks pass.
The corrected native workflow passed all 80 regressions and five filesystem
stages; all three reports confirm success and cleanup. Installation-specific
enforcement, model/profile capacity and reboot acceptance remain required.
The same disposable wrapper now supports a
[full-length real-model proof](multirun/WORKSPACE_QUOTAS.md#full-length-real-model-storage-proof)
for 50,000 people, 2019–2026, one and three repetitions under 4.5/5.5 GiB hard
limits. `multirun-quota-storage-20261005-101156` passed both cases and all 12 storage
backend checks. The sampled kernel peaks were 2.91/3.11 GiB; all three reports
confirm success and cleanup, and the model report confirms source preservation. Older storage measurements
predate enforcement.
A [256 MiB/twelve-repetition trial](multirun/WORKSPACE_QUOTAS.md#trial-256-mib-per-repetition-twelve-repetitions)
uses a 7 GiB limit. The first native run completed all twelve repetitions with a
5.22 GiB sampled kernel peak, but failed when output verification outlasted the
worker lease; all three reports confirm cleanup. Output verification now renews
owned leases with the original deadlines and fencing. The corrected native run
`multirun-quota-256-12-20261005-135803` passed all 32 storage/worker checks and the
complete twelve-run workflow. Its sampled kernel peak was 5.16 GiB with 1.84 GiB
headroom; one attempt completed and verified all original seeds/eight annual
years, removed repeated inputs and released capacity. All three reports confirm
success and cleanup. The tested **4 GiB + 256 MiB per repetition** is now the
default for newly registered releases; existing recorded policies are preserved.
Its measured evidence covers this 50,000-person, 2019–2026 profile.

## Verified local evidence

All linked reports were read for this review. Links refer to the original files on
Ross's laptop under `/home/rer/simpaths-benchmarks` or `/tmp/codex-rer`; archive
protected copies before retiring that storage. For wrappers, inspect both
`report.json` and the adjacent `model-proof/report.json`: the listed wrapper runs
confirm success and cleanup.
The older real-model drivers use `status: "passed"` rather than that report format.

| What passed | Evidence | Scope |
| --- | --- | --- |
| MultiRun review/submission, draft persistence, YAML, compact sweeps, configuration caps, runtime budgets and owner isolation | [Workflow report](/home/rer/simpaths-benchmarks/multirun-browser-20261001-104331/report.json) | Backend and browser checks with fictional data |
| PostgreSQL SingleRun controls, concurrent owners, downloads and independent Reset/Leave | [Two-session report](/home/rer/simpaths-benchmarks/two-session-postgres-cutover-20261002-193428/report.json) | Two real 20,000-person models |
| Redis/PostgreSQL responsiveness comparison supporting the completed PostgreSQL migration | [Performance report](/home/rer/simpaths-benchmarks/singlerun-state-performance-20261002-093839/report.json) | Three paired full 50,000-person trials, 2019–2026; historical comparison |
| Shared SingleRun/MultiRun admission, polling under load, restart adoption and original-seed completion | [Mixed-load report](/home/rer/simpaths-benchmarks/mixed-load-20261003-000304/report.json) | One real 20,000-person and one real 50,000-person model |
| Retained model versions preserve datasets, accepted reviews, queued jobs, resources and retries | [Release-transition report](/home/rer/simpaths-benchmarks/postgres-queue-20261003-112048/report.json) | Fictional releases; scientific compatibility is assessed separately |
| Repetition-scaled working storage and removal of repeated run inputs while preserving options and verified output | [Storage report](/home/rer/simpaths-benchmarks/multirun-storage-20261003-124138/report.json) | Full real 50,000-person runs, including three repetitions |
| Hard MultiRun workspace quotas, inherited ioctl denial, broker/worker restart, independent restore and saved-result preservation | [Quota report](/home/rer/simpaths-benchmarks/workspace-quota-20261005-011818/report.json) and [filesystem proof](/home/rer/simpaths-benchmarks/workspace-quota-20261005-011818/postgres-proof/model-proof/report.json) | Two disposable XFS filesystems, real kernel limits and fictional Docker models; not installed-host acceptance |
| Live quota growth, pending-change reconciliation and independent restoration of the enlarged limit | [Resource recovery quota report](/home/rer/simpaths-benchmarks/resource-recovery-quota-20261005-233529/report.json) and [filesystem proof](/home/rer/simpaths-benchmarks/resource-recovery-quota-20261005-233529/postgres-proof/model-proof/report.json) | Two disposable XFS filesystems; same container/process/project survives growth and worker/broker restart |
| Incomplete zero-exit output at a confirmed byte quota triggers one larger-storage retry with unchanged seeds/specification | [Storage retry report](/home/rer/simpaths-benchmarks/resource-recovery-quota-20261006-002259/report.json) and [filesystem proof](/home/rer/simpaths-benchmarks/resource-recovery-quota-20261006-002259/postgres-proof/model-proof/report.json) | 96 regressions and seven native XFS stages; fictional work, independent restoration and cleanup |
| Live RAM growth, container OOM, real Java heap exhaustion and larger-heap retry with original seeds | [Native Docker/JVM report](/tmp/codex-rer/resource-recovery-docker-java-check-2/report.json) | Six fictional native fixtures; scientific capacity calibration remains separate |
| Real scientific work survives live RAM/application-storage increases with the same JVM and fixed heap | [Growth report](/tmp/codex-rer/resource-model-20261006-d/report.json) and [model proof](/tmp/codex-rer/resource-model-20261006-d/model-proof/report.json) | Public 50,000-person run, 2019–2026, one attempt, seed 606; test-only 50% pressure threshold; application monitoring |
| Real scientific work survives live kernel quota/RAM increases without restarting its JVM or changing its heap | [XFS growth report](/home/rer/simpaths-benchmarks/resource-model-xfs-20261006-104316/report.json) and [model proof](/home/rer/simpaths-benchmarks/resource-model-xfs-20261006-104316/postgres-proof/model-proof/report.json) | 32 storage/worker checks; public 50,000-person run, 2019–2026, one attempt; same XFS project, 4.25-to-6.640625 GiB storage and 5-to-6 GiB RAM; test-only 50% threshold; cleanup confirmed |
| Large CSV verification preserves status polling and the original worker lease | [Validation driver](/tmp/codex-rer/connection-investigation-20261006/run-e/report.json) and [proof](/tmp/codex-rer/connection-investigation-20261006/run-e/model-proof/report.json) | 280 MiB fictional output; three validations; four polling clients; five-second lease; original attempt/settings/seeds/hashes and owner denials; no connection/HTTP/renewal errors; cleanup confirmed |
| Large aggregates, ZIP hashing/downloads and slow file maintenance preserve protected polling and owned leases | [Background driver](/tmp/codex-rer/background-response-20261006-proof-c/report.json) and [proof](/tmp/codex-rer/background-response-20261006-proof-c/model-proof/report.json) | One real HTTP event loop; 120,000 aggregate rows; 128 MiB fictional output; physical cleanup beyond five-second lease; 1,092 expected replies; no connection/HTTP/renewal errors; original attempt/settings/policy and hashes; capacity release and cleanup confirmed |
| Both production retirement steps remove settled failed/successful containers while preserving diagnostics and completed output | [Retirement report](/tmp/codex-rer/resource-retirement-20261006-a/report.json) and [native check](/tmp/codex-rer/resource-retirement-20261006-a/model-proof/report.json) | Small real Docker workloads and disposable PostgreSQL; repeated cleanup and atomic progress replacement |
| Backup preservation of confirmed increases and safe abortion of pending external changes in the inactive target | [Recovery backup report](/tmp/codex-rer/resource-recovery-backup-check/report.json) | Native disposable PostgreSQL dump/restore and fictional files |
| Background operational history, scheduler isolation, immutable starting profiles, legacy policies and inactive restore preservation | [Monitoring/profile report](/home/rer/simpaths-benchmarks/postgres-queue-20261011-193041/report.json) and [native proof](/home/rer/simpaths-benchmarks/postgres-queue-20261011-193041/model-proof/report.json) | 154 cases, no failures/errors/skips; disposable PostgreSQL and fictional workloads; cleanup confirmed; no scientific allocation recommendation |
| Hot performance-review controls, sampled absolute demand, decimal storage ceilings and saved sampling intervals | [Native proof](/tmp-codex/performance-review-native-20261017-105635/model-proof/report.json), [final interval/history checks](/tmp-codex/performance-review-final-20261017-113932/report.json) and [operator guide](multirun/PERFORMANCE_REVIEW.md) | 179 native proof checks, 35 focused local checks and seven final PostgreSQL checks pass; cleanup confirmed. Fictional counters and captured senders; actual VPS overhead, burst safety and allocation recommendations remain unverified |
| Add configurations without changing earlier execution history, downloads or ready charts; retain completed summaries for later comparisons | [Database workflow](/tmp-codex/experiment-extensions-retry-20261017-122328/report.json), [Chromium/private-service proof](/tmp-codex/experiment-extensions-browser-20261017-122701/model-proof/report.json), [final regressions](/tmp-codex/experiment-extensions-final-20261017-123500/report.json) and [iteration guide](multirun/EXPERIMENT_ITERATION.md) | 84 native database checks, three browser stages and 45 overlapping final database checks pass; cleanup confirmed. Local retention/form checks pass. Fictional results and captured email senders; hosted sizing/UID isolation remain separate acceptance work. Independent aggregate retention and backup coverage are described in the following entry |
| Retained charts survive routine raw expiry, while explicit deletion/revocation denies access; summaries and ready charts survive verified recovery | [Native regressions](/tmp-codex/visualiser-independent-retention-20261017-134749/report.json), [native recovery proof](/tmp-codex/visualiser-retention-backup-retry-20261017-142708/model-proof/report.json) and [retention/recovery guide](multirun/VISUALISER_RETENTION.md) | 127 PostgreSQL checks and 47 native backup/restore checks pass without skips; cleanup confirmed. Migration 029 records provenance/expiry causes; new offline/online backups include committed summaries and ready indexed charts, preserve deadlines and enforce restored permissions. Full-size hosted performance/UID isolation remain separate acceptance work |
| Two 100,000-person/2070 repetitions complete within fixed 5 GiB container RAM and 4 GiB maximum heap | [Case A report](/tmp-codex/research-case-a-fixed-5g-20261011/model-proof/report.json) and [compact evidence](/tmp-codex/research-case-a-disposal-20261011/compact-evidence.json) | Seeds 606/607, 52 annual years, one attempt, no expansion/retry; 4.38 GiB sampled working RAM, 3h31m container lifetime, 9.30 GiB sampled workspace; cleanup confirmed; original evidence retained after bulk-output disposal |
| The same two-seed workload completes with fixed 6 GiB container RAM and 4 GiB maximum heap | [Case B report](/tmp-codex/research-case-b-fixed-6g-20261011/model-proof/report.json), [compact archive](/tmp-codex/research-case-b-compact-archive-20261011/archive.json) and [A/B comparison](/tmp-codex/research-benchmark-comparison-ab-after-disposal-20261011/comparison.md) | 4.21 GiB sampled working RAM; 3h26m47s container lifetime; 1,197 successful status requests, p95 92.9 ms; one attempt without expansion/retry; independent file/year audit and cleanup pass. Eight annual CSVs match per seed; BenefitUnit differences remain warnings. Single timings do not establish a speed benefit |
| The same two-seed workload completes with fixed 6 GiB container RAM and 5 GiB maximum heap | [Case C report](/tmp-codex/research-case-c-fixed-6g-heap-5g-20261011/model-proof/report.json), [independent audit](/tmp-codex/research-case-c-fixed-6g-heap-5g-audit-20261012/report.json), [compact archive](/tmp-codex/research-case-c-compact-archive-20261012/archive.json) and [A/B/C comparison](/tmp-codex/research-benchmark-comparison-abc-archived-20261012/comparison.md) | 38 native regressions; one attempt without expansion/retry; 2h52m53s container lifetime; 4.63 GiB sampled working RAM, 3.92 GiB heap; 6.93 GiB retained output/9.30 GiB workspace; 1,010 successful status requests, p95 79.2 ms; independent 25-file/52-year audit and cleanup pass. C is 16.4% shorter than B in these single observations; heap causality and hosted performance remain unestablished, BenefitUnit warnings remain |
| Full-length real-model completion under kernel quotas, frozen seeds/resources, verified output/input-copy cleanup and released capacity | [Quota storage report](/home/rer/simpaths-benchmarks/multirun-quota-storage-20261005-101156/report.json) and [model proof](/home/rer/simpaths-benchmarks/multirun-quota-storage-20261005-101156/postgres-proof/model-proof/report.json) | Real public 50,000-person runs, 2019–2026, one/three repetitions under 4.5/5.5 GiB XFS limits; disposable local filesystem |
| Twelve repetitions under a smaller allowance, long-output lease renewal, verified publication/input cleanup and released capacity | [Trial report](/home/rer/simpaths-benchmarks/multirun-quota-256-12-20261005-135803/report.json) and [model proof](/home/rer/simpaths-benchmarks/multirun-quota-256-12-20261005-135803/postgres-proof/model-proof/report.json) | Real public 50,000-person runs, 2019–2026, 4 GiB + 256 MiB/repetition under a 7 GiB XFS limit; proof-only policy |
| Two sequential 100,000-person/2070 repetitions, original seeds/settings, live memory growth and verified output/cleanup | [Trial report](/tmp-codex/research-storage-audit-20261010-two-seed-trial/report.json) and [model proof](/tmp-codex/research-storage-audit-20261010-two-seed-trial/model-proof/report.json) | Seeds 606/607 in one attempt; 6.93 GiB retained output, 9.30 GiB sampled workspace under a 12 GiB application-monitored allowance; 5-to-6 GiB container growth with unchanged 4 GiB heap; 4.39 GiB sampled working RAM; 38 database regressions and 1,097 successful status requests; cleanup confirmed |
| Read-only operator inventory of jobs, reservations, storage, cleanup, notices and releases | [Operator report](/home/rer/simpaths-benchmarks/postgres-queue-20261003-142214/report.json) | Disposable PostgreSQL and fictional files |
| Maintained Visualiser page, configuration names, separate alternatives, comparison ZIPs, aggregate-only VM responses and owner/source guards | [Visualiser report](/home/rer/simpaths-benchmarks/vm-visualiser-20261006-153932/report.json) and [browser proof](/home/rer/simpaths-benchmarks/vm-visualiser-20261006-153932/model-proof/report.json) | Real browser and aggregation code with fictional CSVs through the private-file/helper pipeline; nine workflow checks; no uncaught browser errors; cleanup confirmed |
| Updated maintained Visualiser, simultaneous alternatives, seed-paired impacts, named exports and aggregate-only owner/source guards | [Backend log](/home/rer/simpaths-benchmarks/vm-visualiser-paired-20261009-183331/tests.log), [Visualiser report](/home/rer/simpaths-benchmarks/vm-visualiser-paired-20261009-184750/report.json) and [browser proof](/home/rer/simpaths-benchmarks/vm-visualiser-paired-20261009-184750/model-proof/report.json) | 91 focused database cases and nine browser stages; pinned `paired-c` build from `b981df5`; fictional native CSVs; no uncaught browser errors; cleanup confirmed |
| Selected-chart loading, bounded catalogues/views, simultaneous alternatives, named/paired exports, local files and owner/source guards | [Backend report](/home/rer/simpaths-benchmarks/postgres-queue-20261010-183603/report.json), [Visualiser report](/home/rer/simpaths-benchmarks/vm-visualiser-selective-20261010-185859/report.json) and [browser proof](/home/rer/simpaths-benchmarks/vm-visualiser-selective-20261010-185859/model-proof/report.json) | 53 PostgreSQL/helper cases and ten Chromium stages; pinned `selective-e` bundle; fictional native CSVs; navigation/refresh avoid whole-package downloads; no uncaught browser errors; cleanup confirmed |
| Native browser chart reuse, concurrent global cache budgets, fixed-age expiry, bounded hints, storage fallback and sign-out across tabs | [Cache browser report](/tmp-codex/visualiser-cache-browser-20261011-212432/report.json) | 11 real Chromium/IndexedDB stages with tiny fictional responses; no browser errors; cleanup confirmed |
| Compiled charts reuse cached sections after fresh authorisation; real website sign-out clears both chart tabs; charts/exports/local files and owner/source denials remain effective | [Backend log](/home/rer/simpaths-benchmarks/vm-visualiser-cache-20261011-213657/tests.log), [service/browser report](/home/rer/simpaths-benchmarks/vm-visualiser-cache-20261011-215418/report.json) and [browser proof](/home/rer/simpaths-benchmarks/vm-visualiser-cache-20261011-215418/model-proof/report.json) | 25 native database cases and 12 Chromium stages; pinned `cache-b` bundle; zero additional chart transfers on refresh; fictional native CSVs; no browser errors; cleanup confirmed |
| Retained 100,000-person/2070 baseline and alternative, all 52 years, named level/paired exports, refresh and aggregate-only owner guards | [Research comparison report](/tmp-codex/research-comparison-20261010-144946/report.json) and [browser proof](/tmp-codex/research-comparison-20261010-144946/model-proof/report.json) | Real completed scientific outputs and Chromium; one matched seed; private calibration budgets; identical 124.1 MiB initial/refreshed aggregate responses; originals unchanged, no model reruns or browser errors; cleanup confirmed |
| Selected-chart loading with the retained 100,000-person/2070 results, all years, identical native exports and aggregate-only owner guards | [Selective research report](/tmp-codex/research-comparison-selective-20261010-194701/report.json) and [browser proof](/tmp-codex/research-comparison-selective-20261010-194701/model-proof/report.json) | Real completed outputs and Chromium; pinned `selective-e` bundle; 244.4 KiB initial metadata/chart requests and 606.2 KiB total captured aggregate responses; all three CSVs match previous native exports; originals unchanged, no model reruns or browser/capture errors; cleanup confirmed |
| Visualiser one/two-worker, 2/3/4-GiB and 1/2-vCPU capacity, cold/warm summaries and interrupted cached retry | [Native capacity report](/tmp-codex/visualiser-capacity-bounded-20261016-001241/report.json) and [interpretation](multirun/VISUALISER_PREPARATION.md#first-native-resource-comparison--16-october-2026) | One round, 24 batches/48 tasks, native Docker/cgroup limits; identical output hashes, no OOMs, cleanup confirmed; summary replay with synthetic seeds/alternatives; raw parsing, repeat-order effects, mixed website/model load and safe memory recall remain unverified |
| MultiRun TLS/cookies, private-file denial, large ZIP preparation, slow transfer, restart/resume, deletion and revocation | [MultiRun HTTPS report](/home/rer/simpaths-benchmarks/https-rehearsal-20261004-003741/report.json) | Real local Nginx/TLS and fictional output |
| SingleRun TLS/ownership, controls/charts, long ZIP transfer, disconnect cleanup and restart access | [SingleRun HTTPS report](/home/rer/simpaths-benchmarks/singlerun-https-20261004-071322/report.json) | Real local Nginx/TLS and fictional models |
| Separated SingleRun TLS/ownership, public/private restarts, shared reservations, long transfers and disconnect cleanup | [Separated HTTPS report](/tmp-codex/singlerun-https-rpc-20261014-173506/report.json) and [model proof](/tmp-codex/singlerun-https-rpc-20261014-173506/model-proof/report.json) | Real local Nginx/TLS, PostgreSQL and native same-UID Unix framing; fictional model infrastructure; cleanup confirmed; installed two-UID checks remain |
| Rebuilt Quick Start 20k/50k, four training modes and user-data browser acceptance through separate public/private services | [Combined refresh report](/tmp-codex/simpaths-security-refresh-user-data-20261014-203101/report.json) and [user-data report](/tmp-codex/simpaths-security-refresh-user-data-20261014-203101/interactive/user-data/report.json) | Real Docker models, PostgreSQL and Chromium; immutable images/runtime/input hashes and four retained training report checksums verified; two user-data Builds, unsafe SQL rejection, CSV downloads and AI revocation pass; no page errors or unexpected AI requests; cleanup confirmed; same-UID fixture, installed host checks remain |
| SingleRun public-upload preparation with 3/4 GiB worker heaps, verified worker exit and two full simulations per case in fixed 5 GiB containers | [Memory comparison report](/tmp-codex/user-data-memory-20261016-120123/report.json) and [comparison](/tmp-codex/user-data-memory-20261016-120123/comparison.md) | Four genuine 50,000-person/2019–2026 simulations, 4 GiB application heap, two CPUs, swap disabled; 3.559/3.505 GiB sampled working RAM; both preparation workers per case exit before Build; reset/rebuild, annual summaries, permissions and private shutdown/cleanup pass. Single ordered comparison; wider uploads/horizons and hosted load remain unverified. User-data catalogue subsequently updated to request the tested 4 GiB application heap/5 GiB container |
| STARTTLS sign-in/completion mail, durable delivery retries, expiry warnings, actual file deletion and reader protection | [Mail and retention report](/home/rer/simpaths-benchmarks/mail-retention-20261004-080335/report.json) | Local SMTP server and disposable files; not external inbox delivery |
| Native online backup/restore, shared SingleRun saved exports, ownership and recovery gates | [Online backup report](/home/rer/simpaths-benchmarks/postgres-queue-20261003-191224/report.json) | Disposable native PostgreSQL and fictional files |
| Scheduled live capture, encryption, pinned SFTP, interruption/retry, retrieval and native isolated restore | [Scheduled backup report](/home/rer/simpaths-benchmarks/backup-rehearsal-20261004-183630/report.json) | Real local systemd/OpenSSH/GnuPG; fictional output |
| Automatic application restart, original-container adoption, retained retry budgets and bounded restart attempts | [Service recovery report](/home/rer/simpaths-benchmarks/service-recovery-20261004-101159/report.json) | Real transient user systemd units and fictional Docker models |
| Restricted database-role permissions, orderly PostgreSQL stop and crash, safe denials and reconciliation | [Database recovery report](/home/rer/simpaths-benchmarks/postgres-recovery-20261004-213531/report.json) | Real disposable PostgreSQL and fictional models |
| Compatible application update/rollback, retained ownership/work/results, rejection of newer schemas and verified recovery | [Separated update report](/tmp-codex/application-update-rpc-retry-20261014-230649/report.json) and [verification](/tmp-codex/application-update-rpc-retry-20261014-230649/verification.json) | Four native processes, production Unix RPC and transient user units; fictional models; original containers/cookies/attempts/download hashes preserved; gated isolated schema restore and owner denials; cleanup confirmed; installed identities and pending large ZIP builders remain separate |

### Evidence added to this review — 16–19 October

The following report outcomes were reread on 19 October. Earlier failed reports
remain preserved; later successful fixtures do not turn those reports into passes.

| Capability / measurement | Evidence | Scope and remaining limit |
| --- | --- | --- |
| Portable complete chart download and offline reopening | [Private-service Chromium proof](/home/rer/simpaths-benchmarks/vm-visualiser-portable-retry-20261016-110048/model-proof/report.json) | Passed; downloaded comparisons recreate charts after server raw-output deletion. Same-UID local services; installed-host identities still require acceptance |
| Raw CSV to intermediate summaries | [36-case raw-summary matrix](/tmp-codex/visualiser-summary-matrix-20261017-234536/report.json) | Campaign completed with 34 passes and two worker failures, retained explicitly. Two genuine repetitions per configuration; failed cases are not complete-run performance observations. See [interpretation](multirun/VISUALISER_PREPARATION.md) |
| Finished comparison/chart regeneration from retained summaries | [108-case regeneration matrix](/tmp-codex/visualiser-regeneration-matrix-20261017-174945/report.json) | Passed, cleanup confirmed; 2/2.5/3 GiB, CPU/worker settings and replayed 2/5/10-seed workloads. Replays measure processing load, not newly independent scientific simulations |
| Seed-parallel preparation and admitted worker scaling | [18-case scaling report](/tmp-codex/visualiser-scaling-20261018-113148/report.json), [production integration](/tmp-codex/visualiser-production-parallel-final-20261018-192819/report.json) and [guide](multirun/VISUALISER_PREPARATION.md) | Passed with cleanup; native group limits and shared admission verified separately from genuine two-seed processing and publication-backlog replays. Hosted disk contention and full ten-seed mixtures remain open |
| Direct stopped-model resource handover and baseline-first scheduling | [Handover runtime proof](/tmp-codex/visualiser-handover-native-20261018-200716/report.json), [89-check baseline/admission proof](/tmp-codex/baseline-first-native-20261018-210920/report.json) and [behaviour](multirun/VISUALISER_HANDOVER.md) | Passed with cleanup; permission/readiness rechecks, continuation, capacity release and restart compatibility use fictional results. Hosted throughput and installed service boundaries remain open |
| Pressure eviction, live storage approval and reader/recovery protection | [54-check final native proof](/tmp-codex/visualiser-storage-native-final-20261018-215233/model-proof/report.json) and [storage guide](multirun/VISUALISER_STORAGE.md) | Passed without skips, cleanup confirmed; fictional files and captured senders, not real inbox or hosted-storage evidence |
| Encrypted restoration of pending and adopted preparation work | [30-check recovery proof](/tmp-codex/handover-encrypted-recovery-final-20261018-221156/model-proof/report.json) and [guide](multirun/HANDOVER_RECOVERY.md) | Passed without skips, cleanup confirmed; real PostgreSQL/GnuPG with fictional results and temporary keys. Actual remote provider transfer/retrieval remains open |
| Population/horizon resource profiles | [Six-case campaign](/tmp-codex/population-year-resources-20261018-235354/report.json) and [measurements](multirun/POPULATION_RESOURCES.md) | All six passed: 20k/50k/100k requested people, ending 2035 and 2070, one seed each, fixed 4 GiB heap/5 GiB container, 2 vCPUs; original settings, annual output, audits, archives and cleanup verified. Observed peaks do not qualify smaller limits |
| Smaller-memory same-JVM smoke | [Native smoke](/tmp-codex/population-memory-smoke-20261019-100139/report.json) and [campaign guide](multirun/MEMORY_TRIALS.md) | Passed: 20k short horizon, two sequential seeds in one JVM, 2.5 GiB heap/3.5 GiB container. The six longer cases completed separately below |
| Smaller-memory same-JVM matrix | [Six-case campaign](/tmp-codex/population-memory-20261019-231601/report.json), [readable tables](/tmp-codex/population-memory-20261019-231601/memory-comparison.html) and [interpretation](multirun/MEMORY_TRIALS.md#measured-results--20-october-2026) | All six passed in 8 h 30 min: two sequential seeds per JVM, fixed 2 vCPUs, heaps/containers 2.5/3.5, 3/4 and 3.5/4.5 GiB as specified. No growth/retries, all native cleanup/audits and compact archives verified. Heap reclaimed between repetitions; two repetitions do not establish ten-repetition stability, lower-CPU safety or hosted concurrency |
| Optional performance timeline/control/recovery implementation | [33-check database proof](/tmp-codex/performance-monitoring-native-20261019-115211/report.json), [50-check native runtime/recovery proof](/tmp-codex/performance-runtime-final-20261019-121537/model-proof/report.json) and [28-check timing follow-up](/tmp-codex/performance-timing-native-20261019-122218/report.json) | Passed with cleanup; fictional work and retained numeric observations. Detailed Java is selected at launch; live external toggles leave running work intact |
| Quiet fictional monitoring cost | [15-second report](/tmp-codex/monitoring-cost-quiet-20261019-124321-15s/model-proof/report.json), [5-second report](/tmp-codex/monitoring-cost-quiet-20261019-124321-5s/model-proof/report.json) and [interpretation](multirun/PERFORMANCE_REVIEW.md#quiet-fictional-monitoring-cost-trials--19-october) | Both passed, 18 jobs in total, identical fictional result fingerprints and cleanup. No clear full-review slowdown on that fixture; not proof of zero cost or scientific/hosted overhead |
| Genuine scientific monitoring smoke | [Model report](/tmp-codex/monitoring-simpaths-smoke-retry-20261019-143815/model-proof/report.json), [cleanup](/tmp-codex/monitoring-simpaths-smoke-retry-20261019-143815/report.json) and [interpretation](multirun/PERFORMANCE_REVIEW.md#genuine-scientific-monitoring-smoke--19-october) | All three modes passed: 100k requested people, 2019–2021, seed 606, fixed 2 vCPUs/4 GiB heap/5 GiB container, one attempt each. Full Java coverage, 37 records without loss, and cleanup confirmed. Only BenefitUnit.csv differed; other eight CSVs matched. This functional smoke does not establish overhead |
| Quiet genuine scientific monitoring cost | [Nine-job model report](/tmp-codex/monitoring-simpaths-quiet-20261019-150929/model-proof/report.json), [cleanup](/tmp-codex/monitoring-simpaths-quiet-20261019-150929/report.json) and [interpretation](multirun/PERFORMANCE_REVIEW.md#quiet-genuine-simpaths-monitoring-cost-trials--19-october) | Passed in 5 h 55 min: 100k requested people, 2019–2035, seed 606, three counterbalanced rounds, fixed 2 vCPUs/4 GiB heap/5 GiB container. No clear monitoring slowdown; complete Java logs without loss. Eight CSVs matched across all nine runs; BenefitUnit.csv also differed between off-mode runs. Combined trace capped at 2,000 observations; complete per-run evidence retained. Precise overhead and hosted mixed load remain unqualified |

### Real-model calibration observations

The [starting-allocation report](/tmp/codex-rer/resource-model-20261006-a/model-proof/report.json)
verified complete 50,000-person output at both 3 GiB and 4 GiB maximum heap with a
5 GiB container. Sampled working-RAM peaks were 3.33/3.61 GiB and workspace peaks
were 2.90 GiB. The [heap-retry report](/tmp/codex-rer/resource-model-20261006-c/model-proof/report.json)
verified a real 512 MiB heap exhaustion followed by successful automatic recovery
at 1.5 GiB heap / 6 GiB container, keeping the original settings, seed and budgets.
These two enclosing reports are **failed**: their post-verification progress-file
and retirement assertions exposed harness defects, now corrected and covered by
the passing native retirement check above. They are observations, not additional
passing wrapper runs. Their transient final-verification database-connection
timeouts prompted a [focused investigation](multirun/VM.md#output-verification-and-database-responsiveness).
CSV thread blocking was reproduced and fixed; the passing fictional proof does
not establish the exact cause of the earlier rare handshake timeout or certify
real-model polling on the selected host.
The follow-up [aggregate/maintenance work](multirun/VM.md#background-work-and-heartbeats)
isolates large JSON work and renews leases during maintenance. Cached-read helpers
are bounded separately from model processing; include their configured memory
bound and encoded response buffers in frontend headroom. Repeat aggregate reads,
ZIP transfers, scans and cleanup alongside representative scientific/concurrent
load when the host is selected.
Single trials do not establish general performance or suitable sizes for other
populations/horizons/collectors. No default heap/container setting was changed.

The current hosted Visualiser integration is a pinned **development preview**
for owner-supplied inputs and explicitly verified public training data.
Confidential provider derivatives are ineligible. Online charts display the baseline
and multiple selected alternatives together, using the maintained seed-paired
policy-impact and uncertainty calculations. Native local-folder input and portable
finished-comparison reopening have local browser evidence; use the selected
build's supported formats. See [the connection scope](multirun/VISUALISER.md).

The Visualiser receives only aggregate results from the VM. Separate authenticated
downloads of eligible owner-supplied output are intentional; provider-derived raw
downloads remain denied. Users may select their own locally saved raw files in the
Visualiser. Those permissions must remain distinct during deployment acceptance.

## Prepare a candidate release record

The refreshed 20 October candidate is staged at
`/tmp-codex/multirun-security-release-20261020-213051/state`, with its immutable
release ID, qualified JAR/runtime, 39 default workbooks and preparation adapters
recorded in the adjacent `qualification-plan.json`. It binds the final hardened
image scan and 538 proof source/input files. All 39,446 non-manifest JAR entries
match the accepted SingleRun build. The 71 focused local guards and native
public-input/queued-preparation proof **pass**. Both configurations completed
seeds 606–608 on attempt 1; preparation survived dispatcher replacement on its
original attempt. Invalid-input rejection, provider raw-download denial, retained
model/runner/policy, original inputs and cleanup are verified. The adjacent
`qualification.json` binds the plan and all native evidence by checksum. See the
[current release checkpoint](multirun/RELEASES.md#refreshed-security-candidate--20-october-2026).
Its private registry does not replace the current service selection or any
scientific benchmark. Final release approval and installed-host checks remain due.

### Earlier model and application update evidence

The 14 October MultiRun security candidate is staged in
`/tmp-codex/multirun-security-release-20261014-212942/state` with provenance in
the adjacent `provenance.json`. It uses the accepted user-data Java runtime and
the rebuilt `multirun.jar`. All 39,443 non-manifest JAR entries match the accepted
SingleRun JAR byte-for-byte; the different manifest selects the MultiRun entry
point. Java source/POM hashes match the accepted build snapshot. Native
retained-release preparation and all six short runs passed in the adjacent
`native-proof-retry` directory. Both configurations completed seeds 606–608 on
attempt 1; preparation survived dispatcher replacement on its original attempt.
The prepared receipt retains the release, model, runtime, runner and resource
policy. Malformed population rejection, provider raw-download denial and cleanup
passed. The adjacent `qualification.json` links the reports and retained evidence
by checksum. This qualifies local technical execution; installed-host security,
recovery and capacity checks remain pending. The existing research package and
service default were not replaced.
The first native proof stopped at a fixture filename conflict before preparation
or simulation. Its cleanup completed. The fixture now validates and clears the
malformed population before uploading its valid replacement; 82 focused checks
pass. The failed `native-proof` reports and the passed retry are both preserved.

The application update rehearsal now selects a compatible separated-service
rollback pair, verifies all four lifecycle policies, and uses native Unix RPC
with public-only restarts before full update/rollback. Its 44 update guards and
seven refreshed native stages pass, with cleanup confirmed in
`application-update-rpc-retry-20261014-230649`. See the
[runbook](multirun/VM.md#local-application-update-and-rollback-rehearsal).

**Current update/rollback checkpoint, 20 October:** a reviewed pair with all
**32** batch migrations is frozen at
`/tmp-codex/application-update-plan-20261020-final/plan.json`. It uses the
`e19212b` frontend and `86b91cb1f` hosting code before the current candidate,
with 28 result/RPC/retained/backup contract files required to match exactly.
Fifty-five local guards, all seven native stages and the final complete
source/file-inventory check **pass** in
`/tmp-codex/application-update-current-20261020-final`. Both reports confirm
success and cleanup; `verification.json` records the checksum-linked evidence.
Public restarts preserved private executors; update/rollback preserved running
containers, queued work, cookies, reservations and result hashes. Recovery
preserved retained summaries, indexed charts, approved storage limits and queued
monitoring exports while retaining owner denials. The pair changes dependency/installation
declarations while its public assets remain identical; the report states that
both generations share the qualified candidate dependency environment.
The first native attempt passed the update/rollback stages and cleaned up, then
stopped when the recovery fixture opened a cache before verified activation.
The restore inventory guard remains unchanged; the corrected fixture opens caches
after activation and the first failed reports are retained.
The retry passed all seven stages, including retained-data recovery, then failed
the final inventory check because a helper added 17 Python bytecode files.
Source bytes were unchanged and cleanup completed. Native code directories are
now read-only while services run; no files are excluded from the integrity guard.
A new plan and evidence directory preserve both earlier failed attempts.
Installed service accounts, filesystem/boot restrictions, real HTTPS and hosted
resource capacity remain separate VM acceptance checks. This local proof uses
the same non-root UID and qualified candidate dependency environment throughout.

The historical `82aad9a` frontend has only 22 batch migrations and is still
refused against this candidate. Migrations 026–032 add storage notices,
external review, experiment additions, retained provenance, preparation handover
detailed performance history and a durable monitoring/mail archive. Retained-Visualiser storage backups also have
a v2 format with approved limits and eviction state. Review schema, RPC, result
and retained-file compatibility together. Do not bypass the migration guard or
claim that the October 14 rehearsal qualifies today's pair. The current native
recovery fixture now includes real retained summaries, indexed fictional charts,
approved storage limits and queued monitoring exports. Keep the verified
pre-migration backup/inactive-restore path for changes that cannot roll back by
code alone. Freeze committed candidate sources before preparing another pair.

[`release_record.py`](release_record.py) collects a private inventory of the four
source checkouts, the selected JAR and immutable image references, a retained
release catalogue, prepared receipts, declared database migrations and dependency
files, and the Visualiser bundle/source hashes. It parses source declarations
without importing the application. It makes bounded local Git reads and never
starts models, services, Docker, builds or network requests.

The [12 October candidate summary](/tmp-codex/deployment-release-record-20261012/SUMMARY.md)
records the currently tested research JAR and `cache-b` Visualiser bundle.
The bundle's 16 declared source hashes match the current Visualiser checkout,
although its recorded build revision precedes the later commit. Both identities
remain in the record. The JAR matches the retained default release; its historical
source/build provenance still needs establishing. Case C's active files are not
selected. The tool's 24 checks use only tiny fictional artifacts and temporary
Git repositories, with no models, database or Docker.

After C completed, the [refreshed A/B/C candidate record](/tmp-codex/deployment-release-record-20261012-abc/SUMMARY.md)
adds its original model/driver reports to the evidence references. Both records
preserve their point-in-time scope; neither approves deployment or replaces the
independent output audit and hosted acceptance.

Use a **new directory outside the source/artifact/service-state directories**:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex nice -n 15 python3 deploy/release_record.py capture \
  --simpaths "$HOME/git/SimPathsWeb/SimPaths" \
  --core "$HOME/git/SimPathsWeb/JAS-mine-core" \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --visualiser "$HOME/git/SimPathsWeb/SimPaths-Policy-Impacts-Visualiser" \
  --jar /tmp-codex/research-prepared-100000-20261009-232734/package/model.jar \
  --image sha256:9521b8ec6b52cfb3a33bfb9e69943e504499e467c3b1c3bcba36b7eb160f5bbc \
  --prepared /tmp-codex/research-prepared-100000-20261009-232734/package \
  --visualiser-build /tmp-codex/visualiser-build-20261011-cache-b \
  --catalogue "$HOME/simpaths-multirun-local/releases/catalogue.json" \
  --evidence /tmp-codex/research-case-b-fixed-6g-20261011/model-proof/report.json \
  --output "/tmp-codex/deployment-release-record-$(date +%Y%m%d-%H%M%S)"
```

For the recorded candidate:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex nice -n 15 python3 deploy/release_record.py verify \
  /tmp-codex/deployment-release-record-20261012/record.json
```

Recheck immediately before using a record. A later commit, status change or selected
artifact/manifest change makes verification fail; capture a fresh record after
committing rather than editing old evidence. It records current `HEAD`, tree and
non-ignored status, not dirty-file contents or a source archive. Current checkout
commits alone do not prove which sources produced an older JAR or bundle.

Reads are bounded to 2 MiB per JSON/declaration, 128 MiB per hashed file and
256 MiB total hashing. Prepared input **names and sizes** are checked against the
receipt; the bulk input content hashes are retained as expected values and are
explicitly **not rechecked**. Image IDs are references, not daemon verification.
Installed dependencies, historical compiler/integration inputs, licence/data
permissions, production data approval and host validation remain separate checks.
No private configuration or raw logs are selected. The directory is private, but
contains local paths and release identities; keep it as operator evidence.

## Remaining deployment checks

Leave these items unchecked until their evidence comes from the selected installation.
The runbooks describe the existing commands and configuration; this checklist does
not install services, choose providers or change security settings.

### Release and service scope

- [ ] Record exact SimPaths, JAS-mine-core and JAS-mine-web commits, JAR hashes,
  immutable image IDs, retained release IDs, input receipts, catalogue, dependencies
  and Visualiser build/source manifest. The candidate tool above prepares this
  inventory; select committed sources and verify the installed artifacts/dependencies
  before completing this gate. Retain the previous usable builds/images.
- [x] Qualify the Python/installer update in an isolated environment, including
  FastHTML imports, sign-in/session/permission/browser workflows and resolved
  installed versions. On 20 October, OAuthLib 4.0.0 / pip 26.2.2 passed 59
  PostgreSQL/HTTP tests, 108 native unit checks, iterative/Visualiser browser
  flows and separated SingleRun HTTPS/restarts. The fresh scan matches all 56
  resolved package versions and reports no matching advisories. The shared
  OAuthLib pin now selects 4.0.0. Disposable proof bootstrap and Docker use the
  shared exact installer file; the existing benchmark interpreter is preserved.
  See `JAS-mine-web/docs/python-runtime-qualification.md` for the receipt and
  exact limitations. Verify the actual installed host separately.
- [ ] Build, qualify and scan fresh model-image IDs with the recorded native
  fixes and current advisory databases. Prepare a new audit inventory with the
  selected qualification receipt and actual Visualiser build/lock directory;
  the old audit defaults and retired image receipts cannot close this gate.
  Record remaining findings and the Pebble component decision individually.
- [ ] Record the actual installed Java runtime and its available/unpublished
  update follow-ups. The 21 October offline inventory confirms Temurin 25.0.4+7
  in the selected candidates. Unpublished updates do not block initial
  installation. Qualify a replacement, including its bundled native libraries,
  entrypoint/Pebble handling and affected workflows, before activating a later
  release; retain benchmark runtimes and separate Java/vendor advisory coverage.
- [ ] Review emitted dependencies for the selected browser/calculation/helper
  bundle; the October 15 `cache-b` assessment does not certify later bundle bytes.
- [ ] Select and rehearse a compatible update/rollback pair for the current
  migration and retained-file formats, preserving a verified backup/inactive-
  restore route across incompatible schema changes. Apply all 32 reviewed batch
  migrations on the fresh installation before sharing its pool with SingleRun.
- [ ] Deploy matching frontend/model lifecycle support and verify live release
  restrictions on the installed host. Catalogue v2 reads existing v1 state but
  older v1-only hosting code cannot read v2 after a lifecycle write; include that
  compatibility in the selected rollback/restore route. Confirm current/previous
  rollback protection, the default hourly/30-day retirement policy and recovery
  archive backups. Retain shared Docker image IDs and separate image archives.
  The final local lifecycle/archive-safety proof passed all 205 native checks
  without failures, errors or skips at
  `/tmp-codex/release-lifecycle-final-20261021-171303`; disposable database cleanup
  passed. The earlier 200-check proof remains historical evidence. The
  read-only deployment inventory accepts v1/v2 and
  records lifecycle/rollback metadata, with all 26 inventory checks passing.
  See [release lifecycle](multirun/RELEASES.md#live-availability-controls).
- [ ] Verify licences, notices and permitted redistribution for the selected
  model, dependencies, Visualiser and training inputs before publishing artifacts.
- [ ] Set the initial data/profile scope and account approval rules. Retain the
  current provider restrictions; agree applicable Visualiser release/output rules
  with its maintainers before expanding the preview. Scientific calculations and
  disclosure decisions remain with the model/Visualiser maintainers.
- [ ] Agree and centrally validate the permitted scientific parameter ranges
  with the SimPaths maintainers (security finding SR06). Request/resource/runtime
  bounds do not by themselves establish scientifically valid model parameters.
- [ ] Confirm normal browser use and guidance for the selected release, including
  narrow layout, preparation, sweeps, retries, downloads and Online/local Visualiser use.

### Host storage and capacity

- [x] Create the Contabo account and obtain the initial VPS. Ross supplied the
  provisioning notice for Cloud VPS 12 (2026), Hub Europe, on 21 October.
- [x] Complete first root SSH access and a read-only OS/CPU/RAM/storage inventory.
  The cached Ed25519 host key matches the corrected console observation. The
  21 October report records Ubuntu 24.04.5 LTS, 12 vCPUs, 47.045 GiB RAM, 400 GiB
  virtual disk, ext4 root, no swap and cgroup v2. Detailed host evidence remains
  private.
- [x] Install the dedicated operator public key and verify root SSH access with
  server-password and keyboard-interactive authentication disabled (22 October).
  The operator key fingerprint was checked against the local public-key file;
  keep that fingerprint and other host details in the private installation record.
- [x] Establish and test key-based operator access with a separate administrator
  account and sudo. A fresh key-only SSH login and password-authenticated sudo
  succeeded on 22 October; retain the account details privately.
- [x] Restrict root and password SSH access after the verified administrator login;
  enable UFW with IPv4/IPv6 SSH rules and verify effective policy through a fresh
  IPv4 key-only administrator connection. Confirmation passed and cancelled the
  temporary rollback timer on 22 October.
- [x] Refresh configured Ubuntu package catalogues and review proposed upgrades.
  On 22 October, the preview proposed zero upgrades/installations/removals and three
  phased updates; no manual holds were reported. No phased updates were forced.
- [x] Verify initial SSH/firewall persistence through a controlled reboot. Changed
  boot IDs, a fresh key-only administrator/sudo login, effective SSH restrictions
  and active IPv4/IPv6 UFW rules passed on 22 October; SSH socket activation and
  UFW are enabled. This is initial access acceptance, not application/storage recovery.
- [ ] Verify external IPv6 filtering/reachability. Apply further reviewed host
  updates when available and recheck access, firewall and CPU-mitigation status.
- [x] Record the guest's CPU-mitigation observations after the Ubuntu package
  review and controlled reboot. The SRSO/no-microcode warning remains unchanged;
  guest inventory alone does not establish physical-host mitigation coverage.
- [x] **Reminder requested by Ross on 21 October:** contact Contabo about SRSO
  (CVE-2023-20569) after the Ubuntu update/recheck. Ross submitted a technical
  support ticket on 22 October; the customer panel confirms it is open for the
  selected VPS. Keep the ticket identifiers and screenshot in the private host record.
- [ ] Obtain and review Contabo's host-specific response on the current guest CPU
  mitigation warning, physical host firmware/hypervisor protections and correct
  exposure of mitigation capabilities to the VM. Keep the response with private
  host evidence and revisit this item during the pre-launch review. Private
  installation with public/fictional fixtures can continue while this clarification
  is pending. The 22 October package review left the warning unchanged; resolve and
  review the host-specific explanation before accepting confidential/proprietary
  data. The guest warning alone does not establish missing host mitigations or a
  breach; CPU generation, hypervisor capabilities and the applicable protections
  need a technical explanation from the provider.
- [ ] Record Contabo's response on sustained scientific CPU use and the project
  account/billing/administration arrangements. Funding is agreed; this remains a
  provider/setup check. Preserve the final order specification, term and costs;
  the provisioning notice alone does not establish those details.
- [ ] Select and provision a reviewed storage layout from the actual partition
  geometry. The initial ext4 root does not meet the current SingleRun/MultiRun
  quota requirements: Docker needs classic `overlay2` on quota-enabled XFS, and
  dedicated MultiRun workspaces need XFS project quotas and non-overlapping
  project-ID allocation. Reserve explicit capacity for OS/PostgreSQL, Docker
  images, private inputs/results/Visualiser data and a separate finite backup
  filesystem sized for the selected backup mode. Scheduled direct capture needs
  retained/new compressed encrypted archives, database/export scratch and headroom;
  explicit directory captures and old pending jobs still need a full plaintext
  copy. Streamed remote readback needs no archive-sized downloaded file. Measure
  these peaks and set local retention before choosing application/backup volumes.
  Verify mount dependencies and persistence before importing images or starting
  services. Fresh Docker 29+ defaults to a different image store; select and
  verify the backend required by the reviewed host verifier before use.
- [ ] Enable and inspect the [live scheduler storage budget](multirun/STORAGE_BUDGET.md)
  with reviewed non-job headroom before later private-filesystem growth. Its
  disposable database/runtime/backup qualification passed after a fixture
  correction; verify actual mounted-host growth, unchanged running jobs and
  restart reuse. No physical resizing or
  storage-budget reduction is performed by this controller.
- [ ] Install reviewed artifacts with dedicated service identities and
  operator-controlled code/config, and configure the selected domain. Keep
  host/Docker access private. Record actual usable RAM/disk and CPU sharing.
- [ ] Complete [provider delivery checks](HOSTED_LOAD_TEST_PLAN.md#provider-delivery-checks):
  preserve the dated order/specification and sustained-use clarifications; compare
  guest vCPU/RAM and device/partition/filesystem capacities with purchased values
  using explicit units. Record bounded disk and external network probes and
  their test limits, endpoint/cache conditions and cleanup.
- [ ] Repeat matched scientific, concurrency and aggregation references in at
  least three separated time windows with 15-second monitoring. Separate our
  Docker CPU throttling from reported host steal/contention. Record ordered
  capacity compliance and service-performance outcomes independently, including
  unknown counters, slow/failed runs and underlying-hardware verification limits.
- [ ] Mount and verify persistent private storage outside all static roots. Budget
  the OS, Docker/PostgreSQL data, retained results, caches, logs and separate backup
  storage independently, including temporary copies and free-space reserves.
- [ ] Install and test physical workspace limits on the selected filesystem and
  executor: SingleRun's Docker writable-layer verifier and MultiRun's XFS project
  broker/static launcher. Check excess writes, restart, restore and unaffected
  neighbouring work. Queue reservations and polling do not establish those limits.
- [ ] Verify storage-pressure behaviour, confirmed cleanup and administrator alerts
  on the actual volumes without sacrificing retained inputs, results or history.
- [ ] Confirm with the SimPaths team the initial population size to use as the
  browser default and the sizes researchers typically choose. Check storage growth
  for those populations, representative horizons and collector settings against
  the 4 GiB + 256 MiB-per-repetition standard before opening the service. The current
  twelve-run evidence covers 50,000 people and 2019–2026.
- [x] Complete the local [same-JVM memory matrix](multirun/MEMORY_TRIALS.md): all
  six 2035/2070 smaller-heap/container cases passed two repetitions at 2 vCPUs on
  20 October, with fixed limits, verified output, archived evidence and cleanup.
- [ ] Test selected smaller profiles at 1/1.5 vCPUs with repeated
  runs before deploying them at those quotas; preserve the fixed 2-processor JVM
  setting until the separately planned automatic-detection comparison is qualified.
- [ ] Calibrate Matteo's updated target: 100,000 people, 2019–2070,
  ten seed repetitions per configuration and up to four concurrent users. The
  [private one-repetition trial](multirun/VM.md#research-workload-calibration) and
  native follow-up cleanup passed on 10 October: 4 GiB heap/5 GiB container,
  5.84 GiB sampled workspace and 3.46 GiB retained output. The matching lower-saving-rate
  alternative also passed in one attempt: 3.91 GiB sampled working RAM and 3.46 GiB
  retained output, with original inputs/seed and confirmed cleanup. Read-only
  server aggregation passed in 9.1 minutes, producing all 52 years and a 135.1 MiB
  encoded comparison. The full private retained-output browser proof also passed:
  charts, named level/paired exports, scenario toggles, refresh and permissions
  were verified with a 124.1 MiB authenticated response and unchanged originals.
  The [read-only storage audit](multirun/RESEARCH_STORAGE.md) also passed:
  native hashes and all annual files match; Person/BenefitUnit account for 98.11%
  of retained output, with annual CSV bytes growing from 48.2 to 76.1 MiB.
  A separate 4 GiB fixed + 4 GiB-per-repetition research candidate was tested with
  two seeds on 11 October after verified archival freed enough space. Both seeds
  passed in one attempt: 6.93 GiB retained output, 9.30 GiB sampled workspace,
  4.39 GiB sampled working RAM, live container growth from 5 to 6 GiB with unchanged
  4 GiB heap, no second input snapshot and confirmed cleanup. All 38 native worker/
  recovery checks and 1,097 owner status requests passed. The earlier 60 audit/
  comparison/calibration checks and dry-run also pass. The
  [two-seed read-only audit](/tmp-codex/research-storage-audit-20261011-two-seed/report.json)
  verified all 25 retained files and all 52 annual years in nine CSVs per seed.
  Ten seeds still project to
  about 34.65 GiB retained output per configuration; ten repetitions, different
  policies, kernel quota, hosted performance and four-user concurrency remain
  to measure. One seed cannot estimate uncertainty across repetitions. This does
  not widen the current 50,000-person/2026 service limits or adopt new defaults.
  The original fixed-memory A/B/C cases and later quiet repeats passed. The
  current large-workload starting candidate is 4 GiB heap/5 GiB container;
  the earlier uncontrolled single C timing does not establish a benefit from a
  larger heap/container. Fractional-CPU D/E trials also passed; use the
  [CPU/memory interpretation](multirun/VISUALISER_PREPARATION.md) and hosted
  mixed-load measurements to select throughput rather than assigning an optimum
  from a single run. Growth/retry safeguards remain bounded by admitted capacity.
- [ ] Execute the [hosted load-test plan](HOSTED_LOAD_TEST_PLAN.md): separate
  four-user access from admitted model concurrency, increase load in stages,
  include aggregation/downloads, and agree queue/completion-time expectations.
  Record repeated sustained-load results before choosing shared or dedicated
  processors; budget retained output as well as active scratch.
  Use separate bounded client timing samples for percentiles; the existing
  operational summaries provide means/peaks only. Count active workspace output
  once, distinguish pressure notices from confirmed increases, and repeat close
  performance comparisons before adopting starting profiles.
- [ ] Measure the intended research horizon, repetitions, preparation,
  aggregation and retained-data growth. Increase concurrency only within measured
  CPU/RAM/disk headroom; include interactive slots and backup activity. Laptop
  measurements do not establish capacity for five or ten researchers.
- [ ] Set pool capacity/holdback, runtime/storage policies, upload/queue/cache caps
  and retention from those measurements. Use isolated staging pools for calibration;
  set production capacity before admitting real users. Existing pool capacity cannot
  be silently rewritten, and accepted jobs must keep their frozen limits.
- [ ] Install the opted-in parallel-preparation/handover configuration and
  executor cgroup drop-in. Verify complete child groups remain inside admitted
  CPU/RAM/scratch limits and that cancellation joins them before releasing
  capacity. Test baseline-first fairness, ready-pair priority, compact fallback,
  stopped-model handover, continuation and restarts under actual model/disk load.
- [ ] Set independent raw/summary/chart retention and the Visualiser's total,
  summary, scratch and entry budgets. Exercise 72-hour-unused pressure eviction,
  suppression of automatic recreation, authorisation after routine raw expiry,
  explicit revocation, live storage approval and warning thresholds on host files.
- [x] Complete the three-round genuine scientific laptop monitoring comparison:
  all nine jobs passed at a 15-second interval. Retain the checksum and bounded
  export limitations recorded in the evidence table.
- [ ] For the first VM trial, prepare and verify the Java observer for each
  selected immutable model JAR, then enable external review and new-launch Java
  review at 15 seconds before launching trial jobs. Record control acknowledgement
  and actual Java launch receipts; a requested switch alone does not prove that
  detailed collection started.
- [x] Implement [scheduled admin monitoring exports](multirun/MONITORING_EXPORTS.md):
  transactional preservation before age/row-cap pruning, bounded verified gzip
  outbox, private daily delivery/retries and live controls. Initial native database
  and runtime/online/offline recovery proofs passed; final evidence is in the guide.
- [x] Implement and locally qualify [long-term collation](multirun/MONITORING_EXPORTS.md#offline-collation):
  stable identities/revisions, numbered manifests and an offline SQLite/HTML/CSV
  importer. Tests cover duplicates, reordering, missing/corrupt parts, cumulative
  corrections, late observations and restore forks without silently adding totals.
- [ ] Verify actual recipient/SMTP delivery, attachment volume, headroom and cost
  of the new journal/mail worker on the chosen VM. Older quiet profiling results
  predate this archive addition.
- [ ] Complete VM mixed-load cost checks before deciding when optional review
  should remain enabled. Confirm
  live external acknowledgement, new-process Java selection, missing-counter
  reporting and web/chart responsiveness. Keep emergency-memory loans disabled.
- [ ] Before enabling automatic resource recovery, install the growth-capable
  quota broker and compatible schema/application, then test live growth and
  resource-specific retries on the selected host. Calibrate heap/native headroom,
  pressure thresholds and maximum allocations with representative populations.
  Include SingleRun, aggregation, database and backup activity in shared/physical
  capacity tests. Confirm owner/operator notices and pending-change reconciliation.
- [ ] Check final output verification and polling under the selected host's
  intended scientific/concurrent load. The local CSV thread-blocking fix and
  short-lease proof passed; the exact earlier rare connection-handshake timeout
  was not reproduced. Retain private timestamped HTTP/PostgreSQL logs if it recurs.

### Database security and supervised services

- [ ] Install the [separate public/executor identities](multirun/PRIVILEGE_SEPARATION.md)
  and both systemd units. Prove the public UID cannot access Docker, quotas, private
  state or database/SMTP credentials. Run the configured-host `privilege_probe.py`,
  then verify browser workflows and both independent restart paths. When hosting
  SingleRun, also install its separate `simpaths-singlerun-web` account,
  `simpaths-singlerun.service` public gateway and
  `simpaths-singlerun-executor.service` private application. Check SingleRun's
  socket/UID boundary and independent restart paths separately using the companion
  JAS-mine-web deployment guide; the MultiRun probe does not establish these.

- [ ] Verify private PostgreSQL connectivity and the restricted application role's
  actual grants. Migrate the MultiRun pool before joining SingleRun to the same
  database/pool with its separate registry schema, or partition budgets explicitly.
  A fresh installation uses PostgreSQL directly; no Redis-session cutover is needed.
- [ ] Install and protect the intended persistent signing/reset keys, private DSN
  and SMTP configuration. Verify restart and recovery with the existing cookies;
  keep credentials out of Git, evidence bodies and browser responses. Follow the
  hosted key instructions; fresh local-test key generation is a different workflow.
- [ ] Validate installed systemd units, paths, permissions and hardening. Test boot
  and reboot as well as process failure, database outage and uncertain Docker
  inspection. Verify readiness, original-attempt recovery and capacity reconciliation.
  Transient laptop units do not test the installed host's boot or privilege policy.
- [ ] Configure operator monitoring for external readiness, disk/RAM headroom,
  blocked jobs, cleanup, restart failures, mail outboxes and backup freshness.

### HTTPS and data access

- [ ] Verify public certificates, hostname/DNS and renewal, actual Nginx configuration,
  forwarded headers and protected-cookie settings for each exposed application.
- [ ] Test host/firewall rules for IPv4 and IPv6. Keep application, PostgreSQL,
  Docker and model ports inaccessible externally; restrict operator access.
- [ ] Run fictional-data probes through the actual external URL as anonymous,
  owner and second owner. Deny direct private-file/traversal requests and restricted
  provider/mixed raw downloads, including HEAD/range/resume and saved links.
- [ ] Inspect the deployed Visualiser's browser network/dev tools: aggregate-only
  VM payloads, approved assets and no raw CSV/database/default-data/private-path
  responses. Check deletion/revocation and local-folder viewing without uploads.
- [ ] Interrupt and resume a large download through the installed proxy, then
  verify the full ZIP/checksums and owner denials after restart and source removal.

### Real email retention and backups

- [ ] Test sign-in and job/administrator notices with the real SMTP account and
  recipient inboxes, including sender identity, TLS/authentication and failures.
  Cover six-hour storage-delay/start notices, confirmed resource growth and
  Visualiser pressure warnings. Local SMTP/captured-sender acceptance alone does
  not prove inbox arrival.
- [ ] Test warning deadlines, retries and deletion with disposable staging files.
  Enable automatic deletion for real users only after notice/recovery acceptance.
  Warnings start when recorded; an SMTP outage does not extend deletion deadlines.
- [ ] Configure separate protected backup storage and the real pinned SFTP host.
  Select and test an external provider; Contabo Auto Backup is omitted from the
  initial order by agreement on 21 October. Confirm the destination supports the
  runner's SFTP hard-link publication, bounded read-only SSH `cat` verification
  over the same pinned endpoint, and independently protected retention.
  Verify encryption, independent key recovery, readback, remote retention and alerts.
  Protect the private decryption key separately from the service VM.
  Include committed intermediate summaries, finished/indexed charts, independent
  expiry/provenance, eviction/approved-budget state and preparation transfer
  records. A provider disk snapshot is not evidence of this application's
  consistent database-and-files capture or independent recovery.
- [ ] Restore a downloaded recovery point on an independent inactive target with
  required images and signing/configuration artifacts. Verify original ownership,
  files, seeds, budgets and provider denials; isolate the source before activation.
  Test capture responsiveness under representative load before enabling the timer.
- [ ] Verify offline maintenance, restore and release-update procedures stop both
  public frontends and their private executors. A SingleRun public-gateway restart
  leaves its private application and Docker sessions running. After activation,
  start the MultiRun private pool before a SingleRun executor that shares it, then
  start the public frontends. Confirm model-container state separately.

## Installation and validation order

1. **Prepare the release record.** Keep the local evidence and record the selected
   committed artifacts, dependency qualification, data scope and compatible
   update/recovery procedure. The intended Contabo trial is recorded above;
   domain and final installation details remain to choose.
2. **Provision private staging.** Establish the host/service account, finite private
   storage, workspace enforcement, separate backup storage and host access rules.
   Fix paths and bounded staging budgets before accepting test work that binds to them.
3. **Install immutable artifacts and PostgreSQL.** Verify images/receipts, configure
   the restricted database role, retain hosted signing keys and run configuration
   checks/preflight. Start the MultiRun pool before shared SingleRun admission.
4. **Start restricted services and HTTPS.** Install and validate the actual systemd
   units/proxy/certificates. Configure real sign-in SMTP; initially use only staging
   users and disposable files, with automatic real-user deletion disabled.
5. **Validate data access and ordinary workflows.** Test the external privacy boundary,
   eligible/denied downloads, Visualiser delivery, controls and browser workflow.
   Then measure real workload/storage and choose the supported concurrency limits.
6. **Validate operations.** Exercise installed service/database/Docker failures and
   reboot, real mail/retention, scheduled remote backup, independent restore and
   application update/rollback. Verify preserved ownership and settled capacity.
7. **Review and open access.** Record the outcomes below. Enable only the tested data
   scope, notices, deletion/timer settings and workload limits; approve the intended
   research accounts. Record any intentionally disabled optional component.

An online recovery point preserves capture-time database state and settled files;
it does not checkpoint a running JVM or retain live incomplete output. Recovery
cannot undo user work or emails after capture. Use the existing inactive restore
and source-isolation procedure before allowing the recovered target to dispatch.

## Deployment acceptance record

Complete this section for the actual release/host. Store detailed reports and
private configuration separately from public assets.

| Record | Value |
| --- | --- |
| Initial trial VPS | Contabo Cloud VPS 12 (2026), Hub Europe; provisioning notice supplied by Ross on 21 October. Separate administrator/sudo, key-only SSH restrictions and IPv4/IPv6 UFW rules verified through fresh IPv4 connections before and after reboot on 22 October. No eligible Ubuntu upgrades at initial review; external IPv6 checks and remaining hardening pending. |
| Selected order specification | Checkout screenshots supplied on 21 October show a one-month term, 12 vCPUs, 48 GB RAM, 400 GB SSD, Ubuntu 24.04, three snapshots and an 800 Mbit/s port; £29.28/month including 20% VAT, no setup fee. Retain the actual invoice privately. |
| Funding arrangement | Confirmed by Matteo on 14 October 2026; costs covered by Alliance for Microsimulation and Policy Analysis CIC |
| Acceptance date and operator | Pending |
| Installed source commits and artifacts | Pending |
| Initial guest specification | Ubuntu 24.04.5 LTS; kernel 6.8.0-147-generic; 12 KVM guest vCPUs; 50,514,526,208 bytes RAM (47.045 GiB); 429,496,729,600-byte disk (400 GiB); ext4 root; no swap; cgroup v2. Observed 21 October; throughput and physical-host mitigation coverage unqualified. |
| Private storage/workspace enforcement | Pending; the initial ext4 layout does not establish the required XFS-backed quota and Docker writable-layer limits. |
| Purchased versus observed provider capacities and component checks | Pending; dated order, byte/count inventory, bounded disk/network probes and evidence limits |
| Repeated provider performance and reliability assessment | Pending; at least three UTC windows, matched artifacts/quotas, monitoring, all outcomes and observed service-target results |
| HTTPS origins and installed service/configuration identities | Pending; record fingerprints/references, not credentials |
| Supported profiles, data scope and Visualiser mode | Pending |
| Measured capacity and frozen resource-policy settings | Pending |
| External privacy and browser/download evidence | Pending |
| Boot/outage/update recovery evidence | Pending |
| Real mail/retention evidence | Pending |
| Backup arrangement | Encrypted application backups to a separate provider planned; destination and independent restore pending. Contabo Auto Backup omitted from the initial order (Ross, 21 October). |
| Remote backup retrieval and independent restore evidence | Pending |
| Remaining issues or deliberately disabled features | Pending |
| Access opened by and date | Pending |

## Runbooks and deferred development

- [Native MultiRun installation, shared admission, privacy and measurements](multirun/VM.md).
- [Browser workflow and user guidance](multirun/LOCAL_WEB.md).
- [Visualiser build, eligibility and aggregate interface](multirun/VISUALISER.md).
- [Model release registration and resource policy](multirun/RELEASES.md).
- [Operator status and interpretation](multirun/OPERATOR.md).
- [Backup, encryption, scheduling and isolated restoration](multirun/BACKUP.md).
- [Real-model browser and mixed-load acceptance](acceptance/README.md).
- [SingleRun image build and release workflow](MAINTAINER_GUIDE.md).

SingleRun configuration, hosted signing keys, state and access controls are
maintained in JAS-mine-web's `deploy/simpaths/README.md`, `deploy/SESSION_SECURITY.md`,
`docs/vm-postgresql.md` and `docs/vm-deployment.md`; retain those documents with the
selected frontend release.

Simultaneous-scenario charts, maintained paired calculations, native local input
and portable finished-comparison reopening are implemented with local evidence;
do not carry them forward as unimplemented roadmap items. Optional prefilled
experiment examples and the documentation-aware assistant remain separate later
development. Mixed Online/local comparisons remain a notes-only idea with
statistical comparability concerns. Wider provider/data/chart scope requires its
own scientific and disclosure approval and integration validation.

### Administrator monitoring archive deployment

- [ ] Apply migration 032 with the reviewed private application release; model
  images/JARs are unchanged. Enable archival before initial collection, then
  verify the private recipient/SMTP settings before enabling daily delivery.
- [ ] Use [MONITORING_EXPORTS.md](multirun/MONITORING_EXPORTS.md) to verify hourly
  packing, a received multipart message, offline deduplication/completeness,
  source lineage after recovery, and sufficient database/WAL/archive headroom.
- [ ] Measure actual daily attachment volume and journal/mail overhead under VPS
  mixed load. Previous quiet monitoring trials did not include this new archive.

Administrator-export functional evidence (20 October):
[69-check recovery/runtime proof](/tmp-codex/monitoring-exports-final-20261020-103414/model-proof/report.json)
and [16-check final mail/control proof](/tmp-codex/monitoring-exports-mail-retry-20261020-104114/report.json)
passed without failures; native harness cleanup was confirmed. The focused first
PostgreSQL run passed 82 checks. Local frontend/adapter checks also pass; counts
overlap. Captured senders were used, with no real mail or scientific runs.
