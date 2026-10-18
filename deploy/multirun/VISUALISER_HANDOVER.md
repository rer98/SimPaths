<!-- (C) Copyright 2026, by Ross Richardson

Production stopped-model resource transfer, readiness-driven continuation,
recovery guarantees and native verification commands.
@author ross richardson
-->

# Direct simulation-to-preparation resource handover

## Behaviour

`visualiser.handover = true` enables the private executor's direct transfer.
Legacy configurations default to `false`. It requires automatic preparation,
reusable summaries and enforced helper resource groups. The throughput candidate
enables it; editing that template does not change an installed service.

1. The worker confirms model termination, validates every repetition and renews
   its ownership throughout validation. Uncertain termination, revoked access,
   failed output and unfinished resource changes cannot donate capacity.
2. The successful-completion transaction releases the model reservation and
   creates a pending preparation reservation together, under the common pool
   lock. Another model or SingleRun admission cannot observe a gap between them.
   No files are parsed and no helpers start inside this transaction.
3. The pending reservation identifies its source attempt and root experiment,
   including additions to an existing experiment. It survives a process restart.
4. A preparation slot takes its normal lifecycle/output locks, rechecks current
   permissions and atomically adopts the pending reservation. The actual admitted
   CPU/RAM/scratch budget becomes the enforced helper-group budget.
5. That group prepares the configuration's per-seed summaries, standalone
   estimates and indexed chart sections. The calculation adapter reports each
   configuration ready only when all its exact seed summaries have completed.
6. Before returning capacity, the group checks the experiment again. Newly
   eligible baseline/alternative pairs lead other standalone raw work. The group
   continues through available comparisons and publications without releasing
   its reservation or entering another capacity queue between tasks.
7. When no eligible unclaimed work remains, or preparation fails or is
   backpressured, helpers are stopped/joined and the group is verified empty
   before its reservation releases. It does not wait idle for an unfinished
   simulation or another configuration's summaries.

Each task retains its existing processing deadline. Continuation is also bounded
by the configured per-owner entry limit, so newly submitted additions cannot
extend one admission forever. The configured number of independent preparation
slots and the shared CPU/RAM/disk admission checks still apply.

## Resource choices

- Prefer one **total 5 GiB / 2-vCPU** group when parallel seeds are enabled,
  multiple repetitions exist and the available allocation fits.
- Otherwise use the configured compact allowance (**2.5 GiB** in the candidate)
  with **1–2 vCPUs**, retaining fractional CPU where available. This returns
  surplus model RAM immediately instead of holding it idle for one seed task.
- A small model cannot initially donate more than its original memory allowance.
  Any memory added from the emergency growth allowance returns to that allowance.
- Adoption can enlarge a compact transfer when the shared ledger admits the
  difference, including legitimate use of the primary worker's protected budget.
  CPU demand requests only the missing CPU, rather than counting a funded
  transfer as an entirely new preparation. Only confirmed revocable simulation
  boosts can be reclaimed; running models keep their existing CPU floors.
- Protected SingleRun and emergency model-memory capacity remain protected.
  The transfer is a resource reservation, not a transfer of JVM memory pages or
  a guarantee of physical processor cores on a shared VPS.

A compact group is not enlarged halfway through a calculation. Disk admission
still checks actual free space; retaining raw output does not make those bytes
free simply because the model's working reservation has finished.

## Readiness and permissions

Automatic comparisons wait for both configurations' exact job/attempt/seed
identities to have completed their summaries. Simulation completion alone is
insufficient. A summary-ready marker can make a pair eligible while standalone
chart publication is still finishing on another slot. Active manual requests
retain the existing ability to calculate missing inputs directly.

Waiting automatic pairs do not claim CPU priority while their summaries are
unavailable. Existing ready pairs remain usable while other configurations run.
The scientific calculations, seed pairing, cache identities and public API are
unchanged. Resource provenance and readiness markers are private status fields.
Revocation, explicit deletion and independent raw/aggregate expiry retain their
existing checks.

## Recovery and backups

Migration **030_preparation_handover.sql** adds source provenance and a pending
flag to the existing `processing_reservations` ledger. There is at most one
transfer per source attempt. Duplicate completion receipts cannot create another.

- A failure before completion commit rolls back model release and transfer.
- A crash after commit leaves an accounted pending reservation for recovery.
- A crash after adoption leaves the normal slot reservation charged. Its inherited
  lock and delegated-group recovery must establish that old helpers have stopped
  before the next slot owner releases it.
- Cleanup of pending reservations cannot remove an adopted reservation. Two
  adopters compete under the same pool lock; at most one wins.
- A queued task temporarily locked during admission keeps its pending transfer.
  A concurrent reconciliation cannot refund that capacity before adoption.
- Revoked, already finished and storage-backpressured pending work releases
  unused transfers. Disabling handover lets the existing processor/scheduler
  reconciliation release unstarted transfers; active groups still require their
  normal cleanup fences.

Offline backup's existing idle check includes these reservations. Online backup
copies their database state, and restored inactive copies clear processor
reservations through the existing recovery conversion. Completed scientific
attempts remain completed; their simulations are not rerun to rebuild preparation.
Apply the reviewed migration with the existing deployment/update procedure before
starting code which expects schema 030.

## Verification

Local checks cover continuation through two alternatives in one admission,
waiting/mismatching summaries, fractional CPU, the admission/reconciliation race,
revocation, failure, uncertain scope cleanup, worker callback forwarding and real
Node source-readiness callbacks in sequential/parallel execution. Calculation
rows, seed pairing and summary reuse remain covered by the production-adapter
regressions. The focused local runs passed **103 checks**: eight handover/
continuation, ten scheduler, 13 worker/heartbeat, 18 existing preparation,
seven private parallel-service, 24 retention/authority and 23 real-adapter/
configuration checks. Database-dependent cases were skipped locally and are
not included in that total.

### Native acceptance — 18 October 2026

The native run **passed all 123 suite checks and 21 private runtime/shutdown
checks**, with no skips, failures or errors. Disposable PostgreSQL cleanup was
confirmed. This includes:

- 16 handover/continuation checks: atomic transfer, blocked competing model,
  compact donation, duplicate completion, rollback/retry, competing adopters,
  pending restart recovery, permissions and continuous pair publication;
- 36 queue/migration, 25 supervised-worker, eight protected-processing,
  16 shared SingleRun/MultiRun admission, 12 automatic-preparation and ten
  database backup-fence/snapshot checks;
- 21 private executor/runtime/shutdown checks.

Evidence:
[`visualiser-handover-native-20261018-200716/report.json`](/tmp-codex/visualiser-handover-native-20261018-200716/report.json)
and
[`model-proof/report.json`](/tmp-codex/visualiser-handover-native-20261018-200716/model-proof/report.json).
The backup checks establish database fences and snapshot compatibility; they
do not constitute a new full encrypted backup/restore rehearsal with a pending
transfer. Hosted performance remains a separate acceptance step.

To repeat the native handover/migration/worker/backup proof from the operator's terminal:
It uses fictional inputs and tiny supervised helpers, with no scientific models
or real emails:

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --test-pattern test_processing_handover.py test_postgres.py test_worker.py \
                 test_processing_capacity.py test_shared_vm_pool.py \
                 test_visualiser_automatic.py test_backup.py \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/vm_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements.txt" \
  --output "/tmp-codex/visualiser-handover-native-$(date +%Y%m%d-%H%M%S)"
```

The earlier [parallel-helper kernel proof](VISUALISER_PARALLEL.md) exercises the
unchanged cgroup/child cleanup mechanism. Hosted mixed-load performance and
website responsiveness remain deployment acceptance checks. Selected-baseline
admission preference, lending idle protected aggregation CPU and changing the
independent worker-count ceiling remain separate work.
