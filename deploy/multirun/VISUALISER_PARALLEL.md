<!-- (C) Copyright 2026, by Ross Richardson

Production integration of bounded parallel seed preparation, resource admission,
sequential fallback and native verification commands.
@author ross richardson
-->

# Parallel Visualiser preparation

## Production behaviour

`visualiser.seed_workers` is the maximum number of independent seed calculations
within one preparation. Its default is **1**, preserving sequential execution.
Setting it to **2** enables parallel execution in the real private service, not
only in the benchmark. It requires automatic preparation, reusable summaries and
enforced delegated cgroup limits.

The setting is independent of `visualiser.max_workers`, which controls independent
preparations. Two preparations with two seed tasks each can run four raw parsers
in total, provided both whole-group reservations are admitted. No individual
seed receives the entire group allowance.

For a preparation with at least two repetitions across its selected sources:

1. Try reserving one **total 5 GiB / 2-vCPU** group (or a larger explicitly
   configured base allowance), using the existing shared PostgreSQL ledger.
2. If that cannot fit, try the ordinary single-worker allowance. The candidate
   profile protects **2.5 GiB / 1 vCPU** for this path. A failed larger request
   creates no partial reservation.
3. Start at most two independent seed calculations, each with a unique request
   and output file. Node old space is 1.5 GiB per task in the measured 5-GiB
   parallel profile; all children share the single enforced group limit.
4. Preserve the original run order when combining completed summaries. Report
   monotonic progress from the preparation coordinator, independent of which
   seed finishes first. Baseline differences and uncertainty still use the
   maintained matching-seed calculation.
5. Join every seed task before the existing combination/publication/indexing
   pipeline continues. Completed verified summaries survive interruption for
   reuse; a partial calculation never becomes a finished publication.
6. Stop/reap commands before releasing inherited reader/processor locks. The
   delegated scope verifies all descendants are gone before releasing the actual
   database reservation. Uncertain cleanup retains that reservation.

A single selected repetition uses the ordinary allowance. Explicit
`seed_workers = 1` stays sequential even if its group has two vCPUs. When the
parallel setting is enabled but resources only admit one worker, no thread pool
is needed for seed calculations.

CPU borrowing considers available RAM and scratch too. It does not remove model
boosts for an additional preparation that cannot fit those resources. The
existing model admission opportunity permits the protected sequential worker,
but pauses additional borrowing, including a larger protected-group request.
Running simulations retain their existing minimum-CPU safeguards.

Authority checks are serialised and periodically refreshed while calculations
run, with fresh checks at command boundaries and publication. Cancellation is
also checked while hashing inputs, copying summaries or waiting on striped
seed locks. Each command slot has its own bounded private diagnostic log; logs
and private paths remain excluded from browser responses.

## Candidate Contabo VPS 12 settings

The revised `vm/throughput.toml.example` has:

```toml
[pool]
cpu_millis = 11000
memory_mib = 40448
hold_cpu_millis = 2000
hold_memory_mib = 5120
growth_memory_mib = 2048

[visualiser]
memory_mib = 2560
cpu_millis = 1000
max_workers = 2
seed_workers = 2
```

This leaves the agreed **30 GiB / 8-vCPU** ordinary simulation/preparation budget
after protected SingleRun, primary preparation and growth headroom. A larger
parallel preparation borrows the extra resources from that ordinary budget.
Growth headroom and idle SingleRun reservations are not available for that loan.

Install the reviewed executor resources drop-in with **MemoryMax=12G** and
**CPUQuota=500%** for two potential 5-GiB/2-vCPU groups plus bounded orchestration.
Startup checks the enclosing memory bound before launching service threads.
These are candidate files; writing them does not change installed services or
an existing immutable pool. Use a new pool or the documented drained migration
when changing protected/pool budgets.

Direct atomic simulation-to-preparation transfer is now integrated behind
`visualiser.handover`; see [the handover guide](VISUALISER_HANDOVER.md) for its
continuation, resource and recovery rules. Mid-calculation shrinking and lending
idle protected CPU to simulations remain separate work. The existing preference
interval still applies to unfunded preparation admission; an adopted handover
keeps its accounted reservation through eligible experiment work.

With handover disabled, another admitted task may hold some of the completed model's former
capacity before preparation obtains its reservation. Protected capacity also
remains unavailable for borrowing. If only the protected 2.5-GiB/1-vCPU allowance
fits, preparation starts sequentially. This is an admission-time fallback; it
does not upgrade an already running sequential preparation halfway through.

## Verification

Local checks exercise the production Node adapter against fictional CSVs:
serial/parallel rows and seed-paired uncertainty agree, baseline swaps reuse the
same summaries, failed tasks stop siblings, and cache-lock waits cancel promptly.
Private service checks cover larger reservations, sequential fallback, progress,
revocation, shutdown/restart and retention after uncertain scope cleanup.

Focused local runs on 18 October passed **141 checks**: command/scheduler
guards (17), scope guards (4), automatic preparation (18), private-service
parallel/fallback lifecycle (7), calculation/cache/configuration regressions
(55), production adapter-to-publication integration (6), and retention,
portable-result and backup-file checks (34). The historical-runner-only guard
was skipped against the newer build; PostgreSQL-dependent cases were verified
through the native runs below. These local scope fixtures do not establish kernel
enforcement or hosted performance.

Private logs include `/tmp-codex/production-preparation-unit-final-20261018.log`,
`/tmp-codex/parallel-backend-config-final-20261018.log`,
`/tmp-codex/visualiser-production-integration-final-20261018.log`,
`/tmp-codex/visualiser-service-parallel-final-20261018.log` and
`/tmp-codex/parallel-retention-regressions-20261018.log`.

The first native PostgreSQL run passed all **nine parallel-service checks** and
**25 publication/access checks**, then stopped on two of 11 automatic-preparation
checks. These exposed a production retention regression: unfinished partners
were treated as unauthorised because they had no readable aggregates yet.
`Results.summary_retention_snapshot` now checks the current dataset grant for
pending partners separately from aggregate availability. Completed outputs still
require aggregate authority, preserving explicit-deletion and revocation rules.
The original failed evidence is preserved at
[`visualiser-production-parallel-20261018-190531/report.json`](/tmp-codex/visualiser-production-parallel-20261018-190531/report.json).

The fix passed **24 authority/retention-planning checks and 18 preparation
regressions** locally. New direct authority checks reproduce the pending-partner
failure before the fix and verify current grants, owner revocation and completed
output deletion. A new native check also requires unfinished outputs to remain
unreadable while their completed partner's summaries are protected.

The native follow-up verified the retention correction: all **12 automatic
preparation**, **nine independent-retention**, **eight processing-capacity** and
**11 adaptive-CPU** checks passed. The shared-pool suite passed 15 of 16 checks;
its remaining error came from a fixture constructing `Visualiser` with `__new__`
and omitting the new per-slot resource tracking. The fixture now uses the normal
constructor. Its constructor/reservation/release path passed a local smoke check.
This fixture correction does not change production admission rules. Original follow-up evidence:
[`visualiser-production-parallel-retry-20261018-191505/report.json`](/tmp-codex/visualiser-production-parallel-retry-20261018-191505/report.json).

The final native run **passed all 16 shared-pool checks and all 21 private
runtime/shutdown checks**, with zero failures or errors and disposable database
cleanup confirmed. Across the native runs, the final passing evidence covers
90 suite checks plus 21 runtime/shutdown checks; earlier failed reports remain
preserved. Evidence:
[`visualiser-production-parallel-final-20261018-192819/report.json`](/tmp-codex/visualiser-production-parallel-final-20261018-192819/report.json)
and its
[`model-proof/report.json`](/tmp-codex/visualiser-production-parallel-final-20261018-192819/model-proof/report.json).

To repeat the real PostgreSQL checks and runtime proof from the operator's terminal:

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --test-pattern test_visualiser_parallel.py test_visualiser.py \
                 test_visualiser_automatic.py test_visualiser_independent_retention.py \
                 test_processing_capacity.py \
                 test_adaptive_cpu.py test_shared_vm_pool.py \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/vm_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements.txt" \
  --output "/tmp-codex/visualiser-production-parallel-$(date +%Y%m%d-%H%M%S)"
```

The extended short kernel proof checks two **production** commands overlapping
inside one 5-GiB/2-vCPU group, inherited lifecycle descriptors, zero swap,
descendant cleanup and isolated group OOM handling. It runs tiny helper programs,
with no scientific simulations, emails, database or installed-service changes:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
systemd-run --user --scope \
  --property=Delegate=yes --property=MemoryMax=7G \
  env TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/multirun/processing_scope_proof.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --parallel-seeds \
  --output "/tmp-codex/parallel-processing-scope-$(date +%Y%m%d-%H%M%S)"
```

The native kernel proof **passed on 18 October 2026**. It verified two production
commands sharing the enforced 5-GiB/2-vCPU group, inherited lifecycle guards,
joined cleanup, fractional CPU enforcement, zero swap, descendant cleanup and
group-local OOM containment while the service survived. Evidence:
[`parallel-processing-scope-20261018-190319/report.json`](/tmp-codex/parallel-processing-scope-20261018-190319/report.json).
The PostgreSQL, retention, shared-pool and runtime/shutdown checks are also
complete, with their evidence recorded above. The kernel proof does not measure
scientific model throughput or website responsiveness under hosted load.

The earlier scaling benchmark's 18/18 native cases qualify the measured prototype
workload; they do not replace these production integration checks. Hosted load,
ten genuine repetitions and website responsiveness alongside running models
remain deployment acceptance checks.
