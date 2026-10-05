<!-- (C) Copyright 2026, by Ross Richardson

MultiRun filesystem limits, privileged quota service and native local acceptance.

@author ross richardson
-->

# Hard limits for MultiRun workspaces

The native VM route with `service.dedicated_storage=true` requires an XFS private
volume with enforced project quotas. Every new attempt receives the working-storage
allowance frozen in its job, before inputs are staged or its container is created.
The current default for new releases is **4 GiB fixed + 256 MiB per repetition**.
Existing release/dataset/job policies retain their recorded allowances. A retry gets a new
project with the original allowance; it cannot enlarge the previous attempt's limit.

The ordinary laptop launcher and `dedicated_storage=false` private diagnostics use
the existing monitoring route. They do not prove hard filesystem enforcement. Use
the native rehearsal below to test the new route locally; actual-host acceptance
remains required before deployment.

## What the limit covers

`execution/batch-<attempt UUID>/work` and `request` share one XFS project and one
byte limit. Nested files inherit it. Successful preparation artifacts moved to
`artifacts/batch-<attempt UUID>` retain that project. XFS rejects further allocations
with `ENOSPC` when the project quota is reached. This kernel error can also mean
a full filesystem, so the verification below checks the cause. Allocated blocks count;
this differs from summing CSV file lengths. An additional inode ceiling prevents
unbounded creation of tiny files; it is a host setting, currently at most 100,000.

Attempt identity, dispatch and termination receipts stay outside that project so a
full workspace can still record its failure. Model containers have a read-only
image, a bounded RAM-backed `/tmp` and bounded Docker logs. The project quota does
not bound uploads, release bundles, other retained inputs, processed caches,
PostgreSQL, Docker images or backups. Budget those separately on finite volumes.

New admission counts the full candidate allowance, unused space promised to active
projects and an operator reserve against actual free space. The broker repeats
that check during allocation. Once removal of the exact stopped container is
confirmed, its unused reservation is settled. Its retained files still consume
space and keep their hard quota until ordinary retention/cleanup deletes them.
Projects are never reused and finished limits are never lifted.

## Privilege and process boundary

The web service remains unprivileged with `NoNewPrivileges=true`. A separate,
administrator-owned quota broker accepts a bounded protocol over a private Unix
socket, authenticating the service account's Linux UID. Clients can supply only an
opaque attempt name, frozen byte limit and one supported operation. The broker
uses directory descriptors, rejects links/replaced roots and verifies XFS quota
accounting and enforcement. It has no commands for mounting, formatting, disabling
quotas, executing programs or choosing arbitrary file paths.

Its root-owned ledger binds the filesystem/root inodes, records original limits
and reserves monotonically increasing project IDs before kernel changes. Interrupted
assignment resumes with the same project. Its bounded write probe requires a
quota-exhaustion error (`ENOSPC` or `EDQUOT`), matching kernel quota/accounting,
free space beyond the active promises and reserve, and a successful allocation
outside the limited project after exhaustion. An error code alone never passes.
A journal identifies the only two bounded probe files eligible for interrupted-probe
cleanup. If the broker is unavailable, new dispatch and adoption cannot claim
verification; existing capacity remains held until termination is confirmed.
Previously installed kernel limits continue to apply while the broker is stopped.

An inode owner can otherwise change XFS project/inheritance flags. Therefore a
small **static launcher** adds an inherited seccomp filter before starting the
existing deadline supervisor. It rejects project-changing filesystem ioctls and
unsupported syscall ABIs. Docker's existing seccomp policy remains in force.
The launcher is mounted read-only; its administrator-owned path and SHA-256 hash
are frozen in the attempt's container policy and checked on adoption. Do not
replace it while protected attempts are running. Retain the installed executable
until those attempts have stopped, then validate any replacement before new work.

Supported ABI: native 64-bit x86 or ARM Linux, kernel 5.14 or newer. XFS project
enforcement must already be enabled (`prjquota`). Reserve an exclusive project-ID
range for this broker, including its probe ID. Do not overlap another broker,
Docker's project range or manually assigned projects. The broker refuses a fresh
ledger whose configured range is already used.

Background: [XFS project quotas](https://man7.org/linux/man-pages/man8/xfs_quota.8.html),
[descriptor-bound quota calls](https://man7.org/linux/man-pages/man2/quotactl_fd.2.html)
[Linux XFS attribute permissions](https://github.com/torvalds/linux/blob/v6.12/fs/xfs/xfs_ioctl.c)
and [XFS project-quota error behavior](https://github.com/torvalds/linux/blob/v6.12/fs/xfs/xfs_trans_dquot.c#L816-L820).
Host administrators and the Docker-enabled service account remain trusted; quotas
do not constrain an administrator deliberately changing the host or daemon.

## Installation on the eventual host

These are installation instructions, not application startup actions. Mount the
new private XFS volume with persistent project-quota enforcement using the host's
normal provisioning process. No formatting command for an existing host volume
is supplied here. Check the real mount after reboot as well as initial acceptance.

Create private execution/artifact roots before starting the quota service:

```bash
sudo install -d -o simpaths-online -g simpaths-online -m 0700 \
  /srv/simpaths-online/private/multirun/execution \
  /srv/simpaths-online/private/multirun/artifacts
```

The roots must be siblings on that XFS filesystem, service-owned, mode 0700, and
must not inherit another project. Install reviewed root-owned copies from
JAS-mine-web. Build the guard with the host's compiler/static C libraries:

```bash
sudo install -d -o root -g root -m 0755 /usr/local/libexec
sudo install -o root -g root -m 0555 \
  /opt/simpaths-online/JAS-mine-web/deploy/workspace_quota_broker.py \
  /usr/local/libexec/jasmine-workspace-quota-broker
sudo cc -static -O2 -Wall -Wextra -Werror \
  /opt/simpaths-online/JAS-mine-web/deploy/workspace_guard.c \
  -o /usr/local/libexec/jasmine-workspace-guard
sudo chown root:root /usr/local/libexec/jasmine-workspace-guard
sudo chmod 0555 /usr/local/libexec/jasmine-workspace-guard
sudo install -o root -g root -m 0644 \
  /opt/simpaths-online/JAS-mine-web/deploy/jasmine-workspace-quotas.service \
  /etc/systemd/system/jasmine-workspace-quotas.service
```

Create `/etc/jasmine-workspace-quotas.json`, root-owned with mode 0600. This is a
shape example: replace UID/GID with `id -u simpaths-online` and `id -g simpaths-online`,
and choose an unused project range and measured reserve. `max_bytes` must cover
every retained release's accepted allowance and the maximum newly permitted
repetition count. It is an allocation ceiling, not every job's allocated amount.

```json
{
  "execution_root": "/srv/simpaths-online/private/multirun/execution",
  "artifact_root": "/srv/simpaths-online/private/multirun/artifacts",
  "socket_path": "/run/jasmine-workspace-quotas/broker.sock",
  "ledger_root": "/var/lib/jasmine-workspace-quotas",
  "service_uid": 977,
  "service_gid": 977,
  "project_first": 1000000,
  "project_last": 99999999,
  "max_bytes": 1099511627776,
  "inode_limit": 100000,
  "reserve_bytes": 4294967296
}
```

Protect the JSON and all ancestor directories from service-account writes.
systemd creates the private ledger and root-managed socket directory. Adjust the
service template's allowed write paths consistently if using a different private
root. Its capabilities permit quota changes, controlled ownership and dropping
privilege in the probe; they are not granted to web/model processes. There is no
runtime `sudo` call from the application.

The native TOML uses matching paths:

```toml
[service]
dedicated_storage = true

[workspaces]
quota_socket = "/run/jasmine-workspace-quotas/broker.sock"
guard = "/usr/local/libexec/jasmine-workspace-guard"
```

Start/verify the broker before the normal application preflight:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jasmine-workspace-quotas.service
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python \
  /opt/simpaths-online/SimPaths/deploy/multirun/vm_web.py preflight \
  --config /etc/simpaths-online/multirun.toml
```

Keep the ledger across normal application/host restarts. Deleting it is not a
repair: a fresh ledger on the same filesystem detects the used project range and
refuses allocation. Running monitoring-only attempts cannot be adopted as protected
ones; drain them before changing execution mode. Review broker failures through its
private journal and existing operator/dispatcher status.

## Restore

Project IDs and inodes are host metadata, not portable backup contents. Restore
verified files/database using the inactive-target workflow. Set up a destination
broker for the new roots with a **fresh ledger and an unused project range**.
Use the native target TOML for activation.

After file/row/image verification, activation reads each frozen attempt's byte
allowance from the verified database and applies it to restored work, request and
preparation artifact trees. It refuses links, foreign projects, excessive trees,
mismatched limits and retained data above its original allowance. Failure leaves
`restore-pending.json` in place. Repeating activation resumes with the same
destination IDs, without changing file contents, seeds, attempt counts, keys or
retention dates. Recovered projects are settled until new attempts are explicitly
admitted; restored retained output keeps its hard limit.

The bounded destination volume needs room for the initial inactive copy. Source
isolation and interrupted-model recovery rules remain those in [BACKUP.md](BACKUP.md).
A quota does not make mutable live output a restorable scientific checkpoint.

## Native laptop rehearsal

Requires existing Docker access, `python:3.12-slim`, PostgreSQL test image, `cc`
with static libraries, `mkfs.xfs`, `mount`, `umount`, and 2 GiB free on the temporary
filesystem. It uses two newly created 512 MiB regular loop-image files, root-owned
temporary brokers/guard, a disposable database and small fictional models running
as your normal account. It installs no service, sends no mail and changes no
existing simulation image, dataset, volume or signing key.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
sudo -- /usr/bin/python3 deploy/multirun/quota_rehearsal.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --python "$HOME/simpaths-browser-tests/venv/bin/python" \
  --output "$HOME/simpaths-benchmarks/workspace-quota-$(date +%Y%m%d-%H%M%S)"
```

`sudo` is required only for the disposable mounts and private brokers. Database
tests, Docker models and clients run unprivileged. Only new regular image files
are formatted. Cleanup removes only this rehearsal's containers/temporary paths.
If unmounting is unconfirmed, its paths remain and the report fails instead of
forcing removal.

The proof runs focused executor/configuration/native dump-restore regressions,
then checks the separate post-exhaustion write and filesystem headroom, nested
exhaustion with the broker stopped, ioctl denial, the other owner's completion
and retained ZIP bytes, confirmed capacity release, original
container restart adoption, and reconstruction on an independent filesystem.
Read the outer report and `postgres-proof/model-proof/report.json`. Until it
passes, unit-test success alone does not establish physical enforcement. Repeat
appropriate checks on the eventual host and approved real model.

On 5 October 2026, `workspace-quota-20261005-011818` passed all **80 regressions**
and all five filesystem stages. The unprivileged startup probe allocated exactly
16 MiB and then received `ENOSPC`. The nested model writer and a restored settled
workspace each stopped below the same combined 16 MiB allowance, including their
request and metadata blocks. About 396 MiB remained free and independent 1 MiB
writes succeeded after each failure. The other owner completed seeds 606/607
once; retained ZIP/result hashes, original container/attempt identities and
frozen limits survived broker/worker restart and independent reconstruction.
The outer, PostgreSQL wrapper and filesystem reports all confirm success and
cleanup. This proves the local kernel/container boundary with fictional work;
it does not certify the eventual installed service, filesystem or real model.

The first two runs passed 74 regressions and cleaned up but stopped at the
startup probe before launching models. The probe incorrectly required `EDQUOT`;
XFS returned `ENOSPC` at the project limit. The corrected check also requires
quota readback, filesystem headroom and a successful independent allocation.

## Full-length real-model storage proof

The same mount/broker wrapper can run the existing storage proof with verified
public 50,000-person inputs. Stop local models first. This mode needs **6 GiB
available RAM** and **10 GiB free on `/tmp/codex-rer`'s filesystem**. It creates
one new 8 GiB regular loop-image file. The installed scientific image and prepared
inputs are reused without rebuilding or modifying them. The model and disposable
PostgreSQL run as the ordinary account; no service is installed and no mail is sent.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
sudo -- /usr/bin/python3 deploy/multirun/quota_rehearsal.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --python "$HOME/simpaths-browser-tests/venv/bin/python" \
  --prepared /tmp/codex-rer/multirun-prepared-50000-20260925 \
  --output "$HOME/simpaths-benchmarks/multirun-quota-storage-$(date +%Y%m%d-%H%M%S)"
```

It runs one and three repetitions sequentially for 2019–2026, under the current
**4.25 GiB and 4.75 GiB hard limits**. Use `--storage-per-repetition-mib 512`
to repeat the earlier 4.5/5.5 GiB calibration explicitly. The startup probe must demonstrate physical
enforcement before either model launches. Each case checks the original project
ID and kernel allowance, frozen Docker policy/guard, complete annual CSVs,
expected seeds and stored output hashes. Normal successful-attempt cleanup must
remove the redundant inputs, preserve options/CSV hashes, remove the exact stopped
container and release both queue capacity and unused physical reservations.
The retained output must still report its original hard limit after staging
directories have been removed. Only then is this proof's output deleted to make
room for the next case.

Read all three reports: `report.json`, `postgres-proof/report.json` and
`postgres-proof/model-proof/report.json`. Kernel usage and file size/allocation
estimates are sampled separately once per second; neither is an exact
instantaneous peak or a RAM measurement. Source, image and prepared-receipt
identities are recorded. This checks compatibility of the selected public model
with the new local boundary, rather than establishing limits for every horizon,
collector setting or dataset. The fictional exhaustion/restore evidence above
remains separate. Repeat appropriate tests on the selected deployment host.

Using the original 512 MiB term on 5 October 2026,
`multirun-quota-storage-20261005-101156` passed all **12 storage
backend checks** and both real-model cases. All three reports confirm success
and cleanup; recorded source hashes match the tested code.

| Repetitions | Hard limit | Sampled kernel peak | Headroom at sampled peak | Retained allocation after input cleanup |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 4.50 GiB | 2.91 GiB | 1.59 GiB | 230.72 MiB |
| 3 | 5.50 GiB | 3.11 GiB | 2.39 GiB | 691.94 MiB |

Both configurations completed in one attempt with their original seeds and all
eight years present in the required CSVs. Redundant inputs were removed
while options and verified output hashes survived. The original stopped containers
were removed, queue/unused physical capacity released and the retained files still
reported their original hard limits. The prepared source remained unchanged.
See the [outer report](/home/rer/simpaths-benchmarks/multirun-quota-storage-20261005-101156/report.json),
[PostgreSQL wrapper report](/home/rer/simpaths-benchmarks/multirun-quota-storage-20261005-101156/postgres-proof/report.json)
and [real-model report](/home/rer/simpaths-benchmarks/multirun-quota-storage-20261005-101156/postgres-proof/model-proof/report.json).

### Trial: 256 MiB per repetition, twelve repetitions

This calibration uses **4 GiB + 12 × 256 MiB = 7 GiB** as the hard working
limit for a single 50,000-person configuration, 2019–2026, seeds 606–617.
It needs **12 GiB free on the filesystem containing `/tmp/codex-rer`** and
**6 GiB available RAM**. The wrapper creates one new 10 GiB XFS loop-image file
there; home holds only the small protected reports and logs. The extra space
allows filesystem metadata, free-space reserve and temporary test dependencies.
Stop local models first and allow roughly three to four hours.

```bash
cd ~/git/SimPathsWeb/SimPaths &&
sudo -- /usr/bin/python3 deploy/multirun/quota_rehearsal.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --python "$HOME/simpaths-browser-tests/venv/bin/python" \
  --prepared /tmp/codex-rer/multirun-prepared-50000-20260925 \
  --repetitions 12 \
  --storage-per-repetition-mib 256 \
  --output "$HOME/simpaths-benchmarks/multirun-quota-256-12-$(date +%Y%m%d-%H%M%S)"
```

These options affect only this disposable proof. The resource policy is copied
and recorded separately; registered releases, prepared receipts and accepted
jobs retain their policies. Preflight rejects a prepared receipt with a different
frozen allowance. Cases must be ordered and unique, with at most 24 runs total
and a bounded 1–1024 MiB term. The filesystem is sized from the selected maximum
allowance before mounting. The proof timeout scales with the planned runs;
other proof callers keep their default two-hour timeout.

The trial runs the storage backend and worker checks before kernel/output/cleanup
verification. All 42 focused local calibration, adapter, allocation and timeout
checks passed before the initial native run. During that calibration the deployment
default remained 512 MiB per repetition.

The first twelve-run trial, `multirun-quota-256-12-20261005-113925`, passed the
12 storage backend cases. The model exited successfully after all twelve
repetitions. The sampled kernel peak was **5.22 GiB**, leaving **1.78 GiB** under
the 7 GiB limit. Its output validator returned, but recording the first verified
repetition failed because validation had outlasted the worker's 60-second lease.
This is failed end-to-end evidence, despite successful execution and cleanup;
it does not approve the candidate policy. All three reports confirm cleanup.
See the [failed model report](/home/rer/simpaths-benchmarks/multirun-quota-256-12-20261005-113925/postgres-proof/model-proof/report.json).

The worker now renews every lease it owns while the adapter verifies output and
prepares publication. It rechecks cancellation, permissions and the original
deadline before recording receipts and completing the attempt. A renewal or
ownership failure preserves the unfinished attempt/reservation for recovery;
renewal does not increase its deadline or retry budget. The renewal thread is
joined before completion and before releasing the dispatcher lock. The native
driver now runs the storage and worker suites first and records the worker code
hash. After the fix, all 30 focused local checks pass (eight slow-output/fencing,
13 native-proof and nine test-driver cases); the slow-output regression also
reproduces lease expiry with the previous worker. The corrected native command
runs 32 storage/worker cases, including real PostgreSQL checks, before the
model.

The corrected trial, `multirun-quota-256-12-20261005-135803`, passed all **32
storage/worker checks** and the complete real-model workflow. All three reports
confirm success and cleanup; recorded source hashes match the tested files.

| Repetitions | Hard limit | Sampled kernel peak | Headroom at sampled peak | Retained allocation after input cleanup |
| ---: | ---: | ---: | ---: | ---: |
| 12 | 7.00 GiB | 5.16 GiB | 1.84 GiB (26.2%) | 2.71 GiB |

The configuration completed in **one attempt** in about 119 minutes, including
verification and cleanup. All original seeds 606–617, required annual output for
2019–2026, stored output fingerprints and each run's `options.txt` were verified.
Repeated input copies were reclaimed, the model container removed, and queue
capacity released. Retained output kept its hard limit; source inputs were
unchanged. The successful verification/publication also exercises the lease
renewal fix after the long simulation.

Retained CSV allocations ranged from **230.18 to 231.93 MiB per repetition**,
averaging **230.91 MiB**. The result supports the candidate **4 GiB + 256 MiB per
repetition** allowance for this tested 50,000-person, 2019–2026 profile. At twelve
repetitions it reduces the reservation from 10 GiB to 7 GiB compared with the
512 MiB term; the actual output size is unchanged. It does not calibrate other
populations, longer horizons or collector settings. Following the successful trial,
the operator adopted this standard for newly registered releases and the proof
commands. Existing recorded policies are preserved. Before deployment, confirm the
default and typical initial population sizes used by researchers and measure those
workloads, as recorded in the [deployment checklist](../DEPLOYMENT_CHECKLIST.md#host-storage-and-capacity).
See the [outer report](/home/rer/simpaths-benchmarks/multirun-quota-256-12-20261005-135803/report.json),
[PostgreSQL wrapper report](/home/rer/simpaths-benchmarks/multirun-quota-256-12-20261005-135803/postgres-proof/report.json)
and [real-model report](/home/rer/simpaths-benchmarks/multirun-quota-256-12-20261005-135803/postgres-proof/model-proof/report.json).

SingleRun keeps its separate Docker writable-layer quota verifier, described in
JAS-mine-web's `deploy/SESSION_SECURITY.md`. This bind-mount implementation does
not certify the chosen SingleRun storage driver or its physical host limits.
