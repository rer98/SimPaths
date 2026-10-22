<!-- (C) Copyright 2026, by Ross Richardson

Revocable idle SingleRun CPU loans, admission guarantees and native verification.
@author ross richardson
-->

# Lending idle SingleRun CPU

The private MultiRun dispatcher can lend unused protected SingleRun CPU to
already running adaptive simulations. A SingleRun launch recalls those boosts
before acquiring its shared-pool reservation. This uses the existing live Docker
quota update and verification path; it does not restart models or change their
JVM settings, scientific parameters, seeds or deadlines.

## Resource rules

- Ordinary admission excludes the full SingleRun holdback. Borrowed CPU can
  improve existing jobs; it cannot admit another simulation or processor.
- SingleRun RAM and storage remain protected throughout. No emergency-growth
  memory, primary Visualiser allowance or general-service budget is lent.
- Every adaptive simulation keeps its reviewed 1-vCPU floor and 2-vCPU ceiling.
  Existing owner fairness and accumulated waiting credit distribute half-vCPU
  boosts. Borrowing uses the configured CPU holdback, rounded down to half a
  vCPU; it does not encode a particular provider or machine size.
- Any SingleRun reservation in the pool disables lending, across all connected
  SingleRun registries. Starting, ready, stopping and failed sessions retain
  their reservations. Pausing the Java model leaves its session `ready` and
  does not make its CPU lendable. Confirmed session removal releases the slot.
- A feasible queued simulation withdraws loan boosts before ordinary admission.
  Imminent unfunded Visualiser work also withdraws them before requesting its
  normal processing reservation. Existing preparation priority remains in force.

For the VPS 12 candidate, the guaranteed ordinary model budget stays **8 vCPUs /
30 GiB**. With six 5-GiB models, idle SingleRun CPU can raise the total model
quota from eight to ten vCPUs: four models at 1.5 and two at 2 vCPUs. When
SingleRun needs its slot, those models return to an eight-vCPU total. Different
fairness credit can change which models receive the boosts. CPU quotas on a
shared VPS do not promise dedicated physical cores or a measured speedup.

## Recall and failures

1. SingleRun checks actual shared reservations under the common pool lock. If
   only CPU is lacking, it records a 30-second recall intent and commits. It
   does not insert a session or hold a database lock while waiting.
2. A dedicated PostgreSQL `LISTEN` connection wakes the private dispatcher.
   An executor-local lock serialises quota reconciliation with normal dispatch.
   Reductions bypass the ordinary boost cooldown; output hashing and other long
   file work do not hold this lock.
3. The worker lowers Docker quotas and verifies the same containers and limits.
   The journal charges the larger old/target allowance until confirmation. An
   uncertain or lost reply keeps capacity occupied and is reconciled again.
4. SingleRun retries its short admission transaction, normally at 100-ms
   intervals, for up to five seconds of recall waiting. It reserves and starts
   only when the verified capacity fits. Database/Docker failure can delay or
   reject a launch; the five seconds is a wait target, not a hard end-to-end
   network timeout. A failed launch returns a capacity message for retry.

The recall intent expires so an abandoned launch does not suppress boosts
indefinitely. Expiry never releases an actual CPU reservation. Concurrent
launches still compete atomically for the protected slot. Listener reconnect
rechecks durable intent; shutdown joins the listener before releasing executor
ownership. Recovery does not restart a scientific process merely to change CPU.

### Starting with one vCPU

The current policy waits for the complete catalogue CPU request, normally two
vCPUs. A staged one-vCPU launch is a possible later optimisation if installed-host
measurements show a meaningful delay between recovering the first and second
vCPU. It would need its own enforced one-vCPU container limit, durable accounting
for a pending upgrade, background recall and a verified increase to two vCPUs.
It must also define what Build/Run does while that increase remains unavailable.
The current loan implementation does not introduce staged SingleRun limits.

## Configuration and operator status

Batch migration **033** adds the opt-in flag and recall timestamp. Old migration
files remain unchanged. Apply reviewed migrations and use matching frontend and
executor code before enabling the feature; a pre-033 application cannot simply
roll back over the newer database. Keep a verified pre-update backup.

```toml
[scheduling]
adaptive_cpu = true
lend_idle_interactive_cpu = true
```

The general configuration default is `false`; the candidate
[throughput configuration](vm/throughput.toml.example) enables it. Enablement
requires adaptive scheduling and a positive protected CPU holdback. Both services
must already use the same database and shared pool. Editing a TOML does not
change a running service: startup applies the setting. No model image or
scientific-code rebuild is needed for this host scheduling feature.

The read-only [operator report](OPERATOR.md) includes
`database.resources.idle_interactive_cpu`: enabled state, eligible loan ceiling,
currently borrowed CPU and pending recall. These are reservation quantities,
not measured utilisation. A negative ordinary batch CPU remainder while loans
are active represents revocable boosts; new admissions still use the normal
budget. Last-launch metadata records the selected option separately.

Online backup preserves enablement but clears launch intent in the restored
database after retiring captured execution reservations. Offline backup retains
the bounded timestamp. Neither restore silently lends RAM or activates work
before the usual recovery gate is cleared.

There is one idle notification connection and one listener thread when enabled.
No repeated Docker sampling, database writes or extra monitoring are required
while nothing changes. Notifications carry only fixed-purpose wakeup labels.

## Scope and verification

This stage lends SingleRun CPU to simulation boosts. Direct live loans to
already running Visualiser cgroups, lending the primary Visualiser CPU, reclaimable
RAM loans and automatic host-wide concurrency sizing remain separate work.
Preparation can reclaim simulation boosts through its existing scheduler, but
does not acquire SingleRun's protected CPU as its own processing reservation.

Run the disposable native proof from the frontend checkout:

```bash
cd ~/git/JAS-mine/JAS-mine-web &&
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --docker-tests --proof-only \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/idle_cpu_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements-backup-proof.txt" \
  --output "/tmp-codex/idle-singlerun-cpu-native-$(date +%Y%m%d-%H%M%S)"
```

It uses fictional jobs and tiny helper containers, testing actual Docker quota
recall, lost acknowledgements, original container PIDs and unchanged non-CPU
limits; concurrent launch safety, restart fencing, shared processing priority;
schema/configuration compatibility, status, backup/restore and shutdown. It
sends no real emails and runs no scientific simulations. Installed-host latency
and responsiveness under representative mixed load remain acceptance checks.

On 22 October, the [native proof](/tmp-codex/idle-singlerun-cpu-native-20261022-170946/model-proof/report.json)
passed **164 checks without skips**; its [driver report](/tmp-codex/idle-singlerun-cpu-native-20261022-170946/report.json)
confirms temporary database cleanup. Both actual quota-recall cases, including a
lost Docker acknowledgement, met the five-second admission wait target.
A final review then serialised routine container verification/pressure checks
with quota changes, preventing a concurrent update from appearing as a container
fault. The [focused native follow-up](/tmp-codex/idle-singlerun-cpu-final-20261022-172055/report.json)
passed **90 checks without skips**, including worker recovery and Visualiser
handover, with temporary database cleanup confirmed. Its [retained log](/tmp-codex/idle-singlerun-cpu-final-20261022-172055/tests.log)
records these request-to-admission measurements:

| Native helper case | Time until the requested 2-vCPU SingleRun reservation fits |
| --- | ---: |
| Ordinary recall | 0.552 s |
| Recall during dispatcher container inspections | 0.546 s |
| One deliberately lost Docker update acknowledgement | 0.753 s |

These cases started with one vCPU already available and reclaimed the missing
CPU from two running helper containers. They therefore measure the extra wait
that a staged one-vCPU launch might avoid. They exclude subsequent Java startup
and use idle fictional helpers on the laptop; they do not establish loaded-VPS
latency. The six-model database checks separately verify two-vCPU loan accounting
and recall. Keep the full reservation policy initially and reassess staged
startup if installed-host measurements show a meaningful delay.

Local verification also passed 41 frontend policy/executor/concurrency checks
and 32 model-owned configuration/status checks. Source whitespace and attribution
checks pass. No scientific image, installed host service or live resource pool
was changed by preparing this feature.
