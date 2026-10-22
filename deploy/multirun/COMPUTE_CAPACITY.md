<!-- (C) Copyright 2026, by Ross Richardson
Installed CPU/RAM detection, administrator approval and running-job preservation.
@author ross richardson
-->

# Live CPU/RAM pool increases

The private executor checks installed CPU/RAM capacity every five minutes,
independently of performance monitoring. Detection records observations and
queues administrator notices. **Only an explicit proposal approval increases the
pool budget.** No simulation, image or scientific-code rebuild is involved.

## Initial installation

Apply reviewed batch migration **034** with the matching host code before enabling
this feature. Older application code rejects the newer schema; take a verified
backup before the update. The candidate `vm/throughput.toml.example` opts in:

```toml
[compute_capacity]
enabled = true
cgroup = "/sys/fs/cgroup/system.slice"
reserve_cpu_millis = 0
reserve_memory_mib = 0
```

The common cgroup must contain both Docker model scopes and preparation helpers.
Verify the actual Docker cgroup driver/parent on the host. A private executor's
own 12-GiB MemoryMax does not cover Docker simulations outside that unit and must
not be mistaken for a limit on their combined pool. The reader applies CPU quotas,
cpusets and memory limits at the configured common cgroup and all its parents.
Missing/invalid metadata for an enabled controller cannot authorise additional
capacity. Inactive child controllers inherit their ancestor restrictions, as
described in the [Linux cgroup documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html#enabling-and-disabling).

At first enablement, zero reserve values save the measured host-minus-pool gap
as service headroom. For the current VPS 12 this preserves the existing budget,
including the RAM left for Ubuntu/PostgreSQL/web services. The SingleRun,
Visualiser and emergency-growth holds stay inside the pool and are unchanged.
Neither current CPU utilisation nor MemAvailable determines that gap.

Enable detection **before upgrading**. If first enabling it after an upgrade,
set reviewed nonzero reserve values: zero would preserve the enlarged host's
entire existing gap. Changing headroom or the common scope after initialisation
requires a separate reviewed policy migration; ordinary approval cannot reduce
those reservations. Providers' GB labels and dedicated-core claims do not
establish actual usable RAM, CPU isolation or throughput.

The generic configuration defaults to disabled. Once a control/approval exists,
restart revalidates it even if initial opt-in is omitted later; this prevents an
old TOML from resetting the approved budget or bypassing capacity checks.

## Detection and email

Two matching readings separated by at least four minutes confirm an increase;
the normal five-minute schedule therefore detects it in approximately five to
ten minutes. Rapid manual checks do not bypass that confirmation. One durable
notice is queued for each newly detected capacity/ceiling, with previous/new
capacity, current/proposed pool budgets, service headroom and the current
per-user limit. Failed mail uses the existing outbox retry path; restarting does
not repeat a delivered notice. Applying the full proposal suppresses a still
pending upgrade notice. No real mail is sent unless normal notification delivery
and the private administrator recipient/SMTP settings are enabled.

The notifier runs inside the private application, not the filesystem-capacity
timer. It needs no scientific job, detailed Java monitoring or performance export.
If the executor is stopped, automatic checks/mail stop too. Independent
[filesystem alerts](HOST_CAPACITY.md) remain separate. No heavy file scans,
Docker stats, provider API calls or throughput benchmarks are performed.

## Administrator commands over SSH

These operate on the installed private configuration. Run from the model checkout
as the private service user, using sudo through the administrator account:

```bash
cd /opt/simpaths-online/SimPaths &&
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python \
  -m deploy.multirun.compute_capacity \
  --config /etc/simpaths-online/multirun.toml status
```

Replace `status` with `check` to refresh metadata and record a notice; this does
not approve an increase or send mail directly. `status` itself makes no changes.
The ordinary operator status also includes detection, headroom and admission state.

Review a proposal:

```bash
cd /opt/simpaths-online/SimPaths &&
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python \
  -m deploy.multirun.compute_capacity \
  --config /etc/simpaths-online/multirun.toml preview
```

Preview records a 24-hour proposal without changing resources. Optional
`--cpu-millis` and `--memory-mib` choose a smaller increase than the safe maximum.
The CPU budget is expressed in thousandths of a vCPU, using 500-unit steps;
RAM is MiB. Both are **whole shared-pool budgets**, including protected holds.
The suggested maximum rounds down spare CPU to half-vCPU and RAM to 256-MiB
steps; it is a capacity ceiling, not a measured throughput optimum.

After reading the returned current/target/protected allowances, apply its UUID:

```bash
cd /opt/simpaths-online/SimPaths &&
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python \
  -m deploy.multirun.compute_capacity \
  --config /etc/simpaths-online/multirun.toml apply \
  --proposal UUID_RETURNED_BY_PREVIEW
```

Apply rechecks the actual host, boot, common scope, original pool snapshot and
service headroom. A changed or expired proposal requires another preview.
Simultaneous approvals/admissions share the same pool row lock; pending Docker
quota changes keep their existing conservative reservations. Repeating an already
applied proposal is harmless. Approvals and observations live in PostgreSQL,
with the original baseline and timestamped applied-proposal history.

## Running work, restart and recovery

An increase can allow more admissions and existing revocable CPU boosts. It
does not alter accepted seeds, models, deadlines, maximum heaps, container RAM,
per-user concurrency limits, two-vCPU simulation ceilings, preparation worker
counts or helper systemd limits. These independent constraints still apply;
review them after a host upgrade. Existing memory-growth rules remain separate.

The original TOML CPU/RAM values remain the baseline. Restart accepts the larger
budget only when the durable approval and actual pool agree. Changing that
baseline, protected holds or unrelated policy still fails the existing checks.
Live disk-budget growth remains independent of CPU/RAM approvals.

If capacity readings fail or the host cannot support the approved pool plus saved
headroom, all new simulation/SingleRun/processor admissions pause. Existing
processes and reservations are retained. The operator receives a capacity warning;
the budget is not reduced. Valid readings restore admission. Actual hardware
removal can still affect running processes; this is not a mechanism for safely
shrinking occupied RAM.

Online and offline native backups include controls and proposal history in their
database snapshot. Restored state retains its existing activation gate. Host
capacity is rechecked before normal startup/admission, and a source proposal
cannot be approved on a different host/boot. Keep installed-host hot-add,
capacity-mail inbox delivery and representative mixed-load tests as acceptance
checks; fictional metadata tests cannot establish provider upgrade behaviour.

## Disposable native proof

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --proof-only \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/compute_capacity_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements-backup-proof.txt" \
  --output "/tmp-codex/compute-capacity-native-$(date +%Y%m%d-%H%M%S)"
```

This uses fictional jobs, captured senders, synthetic host metadata and a disposable
PostgreSQL database. It runs no scientific simulations and sends no real emails.

### Local qualification — 22 October 2026

The corrected focused PostgreSQL run passed 49 checks, including migration
compatibility, concurrent approvals, stale proposals, unchanged active attempts
and captured administrator mail. The combined runtime/recovery proof passed
**175 checks without failures, errors or skips**, with disposable-database cleanup
confirmed: `/tmp-codex/compute-capacity-native-20261022-200233/report.json` and
its `model-proof/report.json`. It includes current runtime assembly, CPU sharing,
original-baseline restart and online/offline native backup/restore. The original
11-error run is retained; it exposed an omitted migration registration, corrected
before the passing runs.

Final review added inactive-controller inheritance and a pending-handover pause
guard. All nine local metadata/policy checks pass. The focused native follow-up
qualifies the latter separately; do not treat the earlier proof as testing later
changes until its own result is recorded.

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --test-pattern test_compute_capacity.py test_processing_capacity.py \
  --output "/tmp-codex/compute-capacity-final-$(date +%Y%m%d-%H%M%S)"
```

A 50-reading check of the actual local metadata reader measured a median wall
time of 0.676 ms, maximum 2.349 ms and 0.0422 seconds total process CPU. Evidence:
`/tmp-codex/compute-capacity-local-20261022/metadata-cost.json`. This measures
metadata reads only, excluding database/outbox work and deployed-VPS performance.
