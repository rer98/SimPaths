<!-- (C) Copyright 2026, by Ross Richardson

Native SimPaths Online VM installation, private storage, recovery and capacity acceptance.

@author ross richardson
-->

# SimPaths Online: UK MultiRun on a VM

## What is prepared

`vm_web.py` runs the same queue, worker, downloads and Visualiser workflow as the
laptop application. It uses an operator-managed persistent PostgreSQL database,
emailed sign-in codes, a canonical HTTPS origin and one native application process.
systemd restarts it after failure or reboot. Closing a user's browser has no effect
on execution. The application adopts surviving model attempts after their leases
expire; it does not create replacement attempts merely because the web process
restarted. An actual VM reboot interrupts model containers: normal recovery decides
whether the job can retry or needs review. Laptop deadline credit is disabled.

The files in [vm/](vm/) are installation templates. No VM/provider has been chosen,
provisioned or modified by adding these files. Real TLS, SMTP delivery, boot/restart,
disk limits, backup restoration and external privacy checks must pass on the chosen
staging VM before public launch. The SingleRun frontend is a separate application
using PostgreSQL as well; it can join the same resource pool as described below.
Do not substitute its Compose template for this MultiRun service.
Use [operator status](OPERATOR.md) to inspect recorded work, shared reservations,
storage, cleanup, notifications and retained releases from the service account.

The optional Visualiser is still the pinned **development levels preview**. It
accepts user-supplied inputs and explicitly imported public training datasets.
Confidential provider derivatives remain ineligible. This preparation does not
choose a disclosure threshold or replace Reese's scientific calculations. Integrate
and test the updated maintained Visualiser release before expanding this scope.

## 1. Install trusted application files

Use a dedicated native Linux account `simpaths-online` and root-managed, reviewed
checkouts at:

```text
/opt/simpaths-online/SimPaths
/opt/simpaths-online/JAS-mine-web
/opt/simpaths-online/venv
/opt/simpaths-online/visualiser/<tested-build>
/etc/simpaths-online/multirun.toml
/etc/simpaths-online/smtp.env
/etc/simpaths-online/postgres.dsn
```

The service account needs access to the local `/var/run/docker.sock`. Docker access
is a privileged host capability: this account and the installed application code
are trusted operator components. systemd protections reduce accidental writes;
they do not turn Docker access into a security sandbox. Never give research users
host logins, Docker access or write access to these code/configuration directories.

Install Python 3.12+, Docker Engine/Compose, Node 18+ if using the Visualiser, Nginx,
certificate tooling and PostgreSQL client tools. Record the actual package/image
versions in deployment evidence. In a fresh virtual environment install:

```bash
/opt/simpaths-online/venv/bin/python -m pip install \
  -r /opt/simpaths-online/JAS-mine-web/requirements-batch-web.txt \
  -r /opt/simpaths-online/SimPaths/deploy/multirun/requirements.txt
```

Use a reviewed MultiRun JAR and parameter workbooks in the SimPaths checkout,
matching the tested model build. Install the already tested user-input execution
image and all training-snapshot source images; there are no implicit image pulls.
Resolve `model.image` to an immutable installed `sha256:...` image ID. The first
serve creates a private frozen release bundle. Subsequent starts verify all retained
versions and the selected default image. Record source commits, JAR hash,
image IDs, training receipts and the Visualiser build manifest for every release.

Use [retained model releases](RELEASES.md) to register/select a compatible newer
version for new inputs while preserving existing jobs/dataset bindings. Keep
`model.image` consistent with the selected release and retain all older images and
bundles. Do not delete `release/` or repoint an image tag as an upgrade shortcut.

## 2. Provision persistent private storage

Mount a dedicated, finite filesystem/volume at `/srv/simpaths-online/private`,
outside the OS filesystem and all public/static directories. Provisioning or
formatting this volume is an operator task, never an application startup action.
Create the private root and `multirun/` subdirectory with mode **0700**, owned by
`simpaths-online`. Give the account read access to copied public training snapshots
under this root, outside `multirun/`. Keep these paths stable across reboot/updates.
No symlink ancestors or temporary `/tmp` prepared inputs are accepted on this route.

The root contains uploads, prepared inputs, execution/raw output, download and
Visualiser caches, frozen model files and private diagnostics. It is never mapped
by Nginx. The default preflight rejects placing it on the OS filesystem. Setting
`dedicated_storage=false` is only for private staging diagnostics; it does not
provide an enforced storage boundary or establish public readiness.

Distinguish these controls:

| Control | What it bounds |
| --- | --- |
| Dedicated private volume | Physical space the service can consume without filling the OS volume |
| `upload_allowance_gib` | Retained uploads per owner; excludes prepared datasets and outputs |
| Queue `storage_mib` | Admission reservations for active work; not retained-data accounting |
| Workspace monitoring | Detects oversized working files and stops the model; not a hard per-attempt quota |
| Download/Visualiser cache caps | Temporary processed-copy budgets with expiration |
| Retention | Release of eligible old files after their warnings and dependency holds |

Before public launch, verify the chosen host's actual filesystem limits and reserve
enough space for all retained data and temporary peaks. Per-workspace hard quota
enforcement is still a host/executor integration requirement; polling alone is not
a substitute. PostgreSQL's named volume and Docker image/log storage are on Docker's
data filesystem, so monitor and budget that filesystem separately. Keep backups on
separate protected storage. A storage outage must leave queue/history intact and
send shared-service alerts only to the configured administrator.

## 3. Persistent PostgreSQL

Install a root-managed copy of `vm/postgres.compose.yaml` and `postgres-init.sql`
in an operator directory outside the source checkout. Supply a reviewed PostgreSQL
17 image digest in `POSTGRES_IMAGE` and a private password-file path in
`POSTGRES_PASSWORD_FILE` through that directory's protected `.env`. The database
binds only `127.0.0.1:5433`, uses a persistent named volume and restarts independently.
Its application role owns its database, with superuser/cluster-management powers
removed by the first-start SQL. Local socket administration is available to the
operator via the passwordless `postgres` role; that role cannot authenticate over
password-protected TCP. Initialization SQL applies only to a fresh volume.

```bash
docker compose -f postgres.compose.yaml config --quiet
docker compose -f postgres.compose.yaml up -d
```

Never use `down -v`, image pruning or an empty replacement database during routine
updates. Inspect readiness and role privileges before starting the web service.
Existing nonempty database volumes require an explicit operator migration; startup
does not overwrite their credentials or privileges.

Create `/etc/simpaths-online/postgres.dsn` as one line, mode **0600**, owned by the
service user. Its content is a PostgreSQL URI or libpq connection string with
database/user/password and the loopback port. Do not put it in Git, command arguments
or screenshots. Remote PostgreSQL requires `sslmode=verify-full` and trusted CA/host
configuration; absent/multi-host or insecure remote connections are rejected.

The stable session secret is generated privately under `multirun/session-secret`.
Keep it across restarts and include it in protected backups. Deleting it invalidates
browser sessions and changes signed review identities; it is not a cleanup target.

## 4. Configure origin, budgets and email

Copy `vm/multirun.toml.example` to `/etc/simpaths-online/multirun.toml` and replace
the domain/image/path values. Run `check` first; it validates configuration without
contacting Docker, PostgreSQL or SMTP, and creates no files:

```bash
cd /opt/simpaths-online/SimPaths
/opt/simpaths-online/venv/bin/python -m deploy.multirun.vm_web check \
  --config /etc/simpaths-online/multirun.toml
```

Unknown settings, HTTP origins, mutable image tags, unsafe paths, invalid quotas
and incompatible retention/email settings are rejected. The example pool admits
one 50k model job plus the default Visualiser processor. It is a conservative staging
allocation, **not** measured production sizing. Leave host/Python/PostgreSQL/cache
overhead outside the pool. For MultiRun-only hosting, these settings cover model
jobs and Visualiser processing. When SingleRun shares the host, give the pool the
total workload budget and configure the interactive frontend with the same
database plus `VM_SHARED_POOL_ID=simpaths-online` and
`VM_SHARED_BATCH_SCHEMA=jasmine_batch`, using its separate `jasmine_vm` schema.
The application role must have the schema/table permissions documented in
`JAS-mine-web/docs/vm-postgresql.md`.

Optional `[pool]` settings `hold_cpu_millis`, `hold_memory_mib` and
`hold_storage_mib` preserve interactive headroom even when no SingleRun session
is open. Raise the total budget accordingly: enough capacity must remain after
holdback for a model and any required processor. Actual interactive allocations
occupying that headroom are counted once. SingleRun admission, batch claims and
Visualiser processors take the same PostgreSQL pool-row lock; allocation and
session insertion/removal commit together. Stopping or failed cleanup keeps the
reservation until workload removal is confirmed. Idle polling does not lock the
common pool.

Drain SingleRun, including owned orphans, before first joining the pool. Its
binding is stored durably so a missing environment setting cannot bypass common
admission later. The standalone local SingleRun bootstrap creates a different
database and therefore does not join automatically. If the services deliberately
use separate pools/databases on one host, partition CPU/RAM/disk explicitly.
Common bookkeeping is not a hard disk quota or a production capacity measurement.

Limits default to 100 configurations, 12 repetitions, 110 unfinished jobs per user,
220 per pool, 4 GiB retained uploads and a 10 GiB prepared-download cache. Runtime
is 15 minutes setup plus 60 minutes per repetition, with three times that cumulative
budget across at most three attempts. Existing jobs keep their frozen runtime.
Queue allowance changes use the existing operator update path. Resource capacities
and execution concurrency of an existing pool cannot silently change: mismatched
settings fail startup. Choose them during isolated staging; later changes need a
reviewed pool transition that preserves accepted jobs/history.

Newly registered release policies scale working storage independently of runtime:
**4 GiB fixed + 512 MiB per repetition**, raising the fixed term for larger input
copies. Twelve repetitions normally need 10 GiB per active configuration, plus
separate retained-data/cache budgets. Submission review checks the capacity after
interactive holdback. Pending datasets have provisional allowances until their
prepared size is known. Already registered fixed policies and accepted jobs are
preserved. See [RELEASES.md](RELEASES.md#resource-policy-interface) for configuration
and the full-length storage measurement command. Measure the largest intended
repetition/horizon/collector workload before raising repetition/concurrency limits;
these estimates and workspace monitoring do not replace physical disk quotas.

Install the SMTP environment from `vm/smtp.env.example` as a root-readable **0600**
file. Use STARTTLS and the real sending address/credentials; implicit TLS on port
465 is not implemented by the shared sender. Email verification is always real on
this VM route: there is no console-code option. Job completion/problem/expiry emails
remain separately configurable and default disabled. Automatic expiry deletion also
defaults disabled and requires notification delivery to be enabled. Configure an
administrator recipient when enabling job notices. See [LOCAL_WEB.md](LOCAL_WEB.md)
for notification routing/deduplication and retention behaviour.

`preflight` checks storage, SMTP settings, database connectivity and the installed
image without sending messages or creating jobs. It does not prove SMTP delivery,
TLS/firewall rules, quotas or scientific compatibility. systemd runs it before serve.
When invoking it manually, load the SMTP settings securely into that process's
environment; a TOML check does not read the SMTP environment file itself.

## 5. HTTPS proxy and supervised startup

Install `vm/simpaths-multirun.service`, then replace all domain/certificate values
in `vm/nginx.conf.example`. Obtain the certificate before enabling its HTTPS
server, verify DNS and test renewal. The ACME directory contains only challenge
files. Validate `nginx -t` and the actual installed unit with `systemd-analyze verify`.
Keep staging restricted to the operator/test users while completing acceptance.

The application listens only on loopback. Nginx proxies all application/static/data
requests; it has no raw-data `alias`, `root`, `try_files`, `X-Accel-Redirect` or cache
mapping. It overwrites forwarded addresses and forwards a canonical Host. Uvicorn
trusts only this loopback proxy. Protected responses keep `Cache-Control: no-store`.
Buffering is disabled for uploads/downloads; normal Range/If-Range/HEAD headers are
passed through. The five-minute proxy timeout is an **idle** timeout, not a maximum
total ZIP transfer time. Large ZIP preparation remains an asynchronous Results-page
operation and resumable downloads retain authentication on every request.

Only SSH from permitted operator addresses and HTTPS/HTTP certificate traffic
should be externally reachable. Do not publish port 5002, PostgreSQL, Docker or
model ports. If serving SingleRun on the same VM, use its separate hostname/service
and complete its own host-firewall/quota acceptance; this proxy does not cover it.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now simpaths-multirun.service
sudo systemctl status simpaths-multirun.service
```

`/healthz` returns only `{"ready":true}`/`{"ready":false}` and 200/503. Readiness
requires a working database connection and a successful dispatcher cycle; it does
not expose credentials or queue entries. Monitor its external URL, systemd status,
free space on both filesystems, SMTP failures, storage waits and pending cleanup.
A fatal dispatcher failure terminates the application so systemd can restart it.
Temporary database/dispatch outages retain jobs/reservations and retry recovery.
Bound journald retention and Docker logs; alert operators rather than pruning data.

The unit caps native Python/Node memory at 3 GiB; model containers have their own
Docker limits outside that cgroup. Recalibrate that cap if processor memory is changed.
Graceful stop waits for active downloads, so updates can take time. `KillMode` also
contains aggregation children. Forced stop/reboot still requires recovery acceptance.

Approve/revoke a researcher from a service-user shell; no admin webpage is exposed:

```bash
cd /opt/simpaths-online/SimPaths
/opt/simpaths-online/venv/bin/python -m deploy.multirun.vm_web approve \
  --config /etc/simpaths-online/multirun.toml --email researcher@example.org
```

Approval lasts 30 days and grants only explicitly imported, verified public training
datasets, never all ownerless provider datasets. Use `revoke` with the same arguments
to invalidate access and existing sessions. User-specific datasets retain their
provenance/ownership checks. Configure/import training snapshots on the first serve
before approval; approval does not import arbitrary new provider data.

## 6. Measure capacity using real 50k MultiRun work

Start with one otherwise-idle staging VM, the actual configured model release,
50,000 people, 2019–2026, two configurations and one repetition, seed 606. Then
repeat with three repetitions. Export the reviewed YAML for each experiment and
retain it beside the evidence. Existing SingleRun timings are context, not proof of
MultiRun/preparation/aggregation/storage capacity.

The recorder's default is a side-effect-free plan:

```bash
/opt/simpaths-online/venv/bin/python -m deploy.multirun.vm_capacity \
  --config /etc/simpaths-online/multirun.toml
```

Submit through the normal reviewed page. While its configurations are queued/running,
run the recorder as the trusted service account with the returned experiment ID:

```bash
/opt/simpaths-online/venv/bin/python -m deploy.multirun.vm_capacity \
  --config /etc/simpaths-online/multirun.toml \
  --experiment EXPERIMENT_UUID --duration 3600 --interval 30 \
  --output /PROTECTED_EVIDENCE/new-capacity-run --execute
```

It reads only selected experiment metadata and the corresponding Docker stats and
workspace sizes, plus host memory/load and free space on the private and Docker
filesystems. Where available it records the native application's systemd cgroup
memory, including Node processing. It never submits, cancels, migrates or deletes anything. Ctrl+C
saves collected evidence without stopping jobs. Raw microdata are not copied;
settings/IDs and measurements are private operational evidence. Samples can miss
brief peaks; unknown linked/missing measurements must not be treated as confirmed
zero usage. Service-wide free space/cache/processor totals include other activity.

Within the recording window:

1. Download output-only ZIPs; record size, preparation time and transfer time.
2. Request comparison and individual ZIPs with inputs, including different-input
   comparisons; interrupt/resume a large ZIP and compare its final checksum.
3. Prepare/open the Visualiser and record processing time and cache size.
4. Repeat with increasing active configuration counts and separately with two
   reserved interactive Quick Start sessions. Observe interference and waits.
5. Test input preparation as a separate workload; monitor its storage/RAM as well.

Start with one active model, then two; use measured headroom before trying the
five-research-user/three-active-configurations-per-user scenario. CPU/memory limits
are ceilings, not guaranteed dedicated hardware. Keep host, database, image layers,
logs, backup, caches and retained-data growth in the storage/cost calculation.
Record exact source commits, images, training receipts, Node/Visualiser revision,
configured limits and host specifications. Do not infer per-repetition storage
growth or large-VM capacity from the earlier two-year laptop runs.

## 7. External privacy acceptance

### Local HTTPS rehearsal before choosing a host

Run the automated rehearsal from the SimPaths checkout:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
~/simpaths-browser-tests/venv/bin/python deploy/multirun/proxy_rehearsal.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web"
```

It creates a disposable loopback PostgreSQL database through the existing test
runner and starts the actual MultiRun HTTP routes behind a real Nginx process.
The proxy uses `vm/nginx.conf.example`, substituting only a temporary `localhost`
hostname, unprivileged IPv4 loopback ports, certificate and ACME paths. Changed
cache/private-file protections stop the adaptation for review. No system proxy
configuration or machine/browser trust settings are installed. The client trusts
only the temporary certificate; normal trust and wrong-name TLS connections must
fail. Service secrets and cookie rules use the existing implementation.

The rehearsal checks:

- Secure sign-in, foreign-origin mutation rejection and two separate owners.
- Private canaries, traversal/dotfiles and server-only Visualiser asset denials,
  both anonymously and while signed in.
- Aggregate-only pair and multiple-alternative responses, including provider
  results whose raw/input/full/range downloads remain denied.
- A real compressed comparison ZIP with retained owner inputs, matching seeds,
  checksums and every run's `options.txt`. Fictional source data exceed the normal
  512 MiB large-download threshold; the threshold is not reduced for the test.
- A deliberately slow transfer lasting beyond 30 seconds, interrupted partway.
  A fresh application process must retain both owner sessions, queued/completed
  jobs and aggregate links, then resume the same ZIP through HTTPS using Range
  and If-Range. HEAD, suffix/invalid ranges and changed validators are checked.
- Source deletion and account revocation invalidating saved/resumed links without
  altering other retained output.

Requirements: Linux, working local Docker access, OpenSSL and at least 2 GiB free
on the temporary-files filesystem. No scientific models run and no emails are
sent. The fixture uses bounded-memory fictional data: by default a 520 MiB source
with a 64 MiB varied tail, giving a sizeable compressed download. All source,
received archive, credentials and certificate files are temporary. Only the
rehearsal's labelled proxy container, random test schema and temporary directories
are removed. Persistent local services and volumes are untouched.

Nginx normally runs in a read-only, unprivileged container, with 128 MiB RAM and
half a CPU; the PostgreSQL runner uses 768 MiB. The `nginx:stable-alpine` test image
is pulled if absent, resolved to its image ID before use and recorded in evidence.
It is a rehearsal dependency, not the production image/version choice. To use an
already installed compatible Nginx instead, supply `--nginx /usr/sbin/nginx`;
its PID/temp files remain inside the rehearsal directory. `--slow-seconds 310`
also tests continuous transfer beyond five minutes; the default is 34 seconds.

Reports and private logs go to a new `~/simpaths-benchmarks/https-rehearsal-*`
directory, or a new `--output` directory. The outer report records disposable
database cleanup; `model-proof/report.json` records proxy checks, archive identity,
restart/resume and cleanup. A pass requires both reports to pass and cleanup to
complete. Cookies, codes, session secrets and the private key are not recorded in
evidence. The report identifies its data as synthetic and its scope as local.

The fixture completes configurations explicitly with fictional files and uses a
synthetic aggregation backend; it does not run the scientific model, the actual
Visualiser charts or the native model dispatcher. Those have their separate
model/browser/VM proofs. This rehearsal does not test a future host's IPv6,
firewall, filesystem quota, systemd/reboot, public certificate, SMTP or backup
destination. Repeat the external checks below on the chosen deployment host.

### Checks against the chosen deployment hostname

Run the existing fictional browser/Visualiser proofs before deployment, then test
the **external HTTPS hostname** after configuring the proxy. `vm_boundary.py`
creates harmless, unique canaries in existing private directories and removes only
those canaries afterward. It probes guessed/private paths, traversal, dotfiles and
server-only Visualiser assets. It never requests or writes real microdata.

```bash
/opt/simpaths-online/venv/bin/python -m deploy.multirun.vm_boundary \
  --config /etc/simpaths-online/multirun.toml \
  --output /PROTECTED_EVIDENCE/new-boundary-run --execute
```

This initial run proves only the unauthenticated path checks. For complete probe
coverage, create fictional provider-derived completed results and prepare a public
training comparison. Sign in as its owner and as a second approved account. Store
each session's complete Cookie header value in a separate service-user-owned 0600
file, outside Git/evidence; the probe never records these values. Add:

```text
--cookie-file /PRIVATE_TEMP/owner-cookie
--other-cookie-file /PRIVATE_TEMP/other-cookie
--experiment FICTIONAL_EXPERIMENT_UUID
--restricted-job FICTIONAL_PROVIDER_CONFIGURATION_UUID
--visualiser-key PREPARED_COMPARISON_KEY
```

The probe requires successful sign-in, retained completed restricted output,
denied full/input/range downloads, strictly validated aggregate rows in either
the pair or multiple-alternative format, and denied
anonymous/other-owner aggregate access. `full_acceptance` is false when these
optional cases are absent, even if the basic path checks pass. Remove cookie files
afterward. Its GET-only checks send no emails and restart no simulations.

Also use the browser through that external hostname to verify every Visualiser
network response, approved static assets, absence of raw/default-data requests and
local-folder viewing without uploads. Probe checks supplement this browser work;
they are not a mathematical guarantee against all future configuration changes.
Repeat privacy acceptance after proxy/storage/application updates.

## 8. Backups, updates and recovery

Before enabling automatic expiry, test both warning stages and real delivery with
dedicated test addresses, then verify deletion guards/recovery using fictional files.
Local testing remains delivery-disabled. Configure administrator-only alerts for
shared disk shortages and owner notices for actionable personal problems.

Back up PostgreSQL **and** the corresponding private filesystem, frozen release,
training snapshots, stable secret, configurations and exact installed build/source
identities. A database dump alone cannot recreate datasets/results. Protect backups
with encryption and access control; never store them in a public/static directory.
Choose a backup retention period that respects the data's own retention rules and
record how expired/deleted private data ages out of backups.

Use the automated [backup and restore runbook](BACKUP.md). The offline command
locks the state and complete queue schema, rejects active/unreleased work, takes
a native PostgreSQL snapshot and copies verified private files and all registered
prepared inputs. It never copies live PGDATA. Download/Visualiser caches are
regenerable and omitted; deployment configuration, Visualiser builds and required
Docker images must be retained separately.

For routine capture while models continue, `create --online` adds matching
immutable files and a repeatable-read SQL snapshot, including shared SingleRun
registries and saved exports in the same database. Active output workspaces and
in-memory models are excluded. Recovery settles interrupted attempts under their
original retry budgets and gates saved SingleRun ownership before activation.
The separate scheduled runner/templates supply public-key encryption, pinned
off-machine copying, retries, local retention and opt-in operator alerts. They
are not enabled automatically. Provision a separate backup volume, keep the
decryption key off the VM, and configure independent monitoring and remote expiry.

Restore uses a new private directory and separate empty database. It verifies
every table row and file, relocates registered input paths explicitly and stays
inactive until verified activation with all required images installed. A matching
interrupted restore can resume. Stop/isolate the original service and containers
before activation; an old backup cannot undo later external work or sent emails.
Do not dispatch copied queued jobs or send copied outbox messages during an
isolated restore exercise. Configure delivery and automatic expiry off initially.
Online capture covers the shared SingleRun registry and closed saved exports;
independent SingleRun databases and model in-memory checkpoints remain separate.

Routine application updates preserve state/database/secrets and drain or adopt
existing work. Keep the previous tested code and build for rollback. Database
migrations are checksummed and append-only: older code may reject a newer schema,
so a schema-changing rollback needs its corresponding verified backup/restore plan.
Do not silently replace database files, reinitialise a schema or run concurrent
application processes for one pool. Keep migration/rollout evidence with the release.

## Validation record

The local configuration/credential/probe/measurement tests, model tests and template
syntax checks are separate from the disposable PostgreSQL VM application proof.
Use `vm_proof.py` through JAS-mine-web's `scripts/test_batch_queue.py` to run the
actual shared application with a fictional executor, captured verification codes,
HTTPS cookies, worker completion, restart, health and fatal-dispatcher recovery.
No real SMTP or scientific simulation is used. Record the returned evidence path.

On 1 October 2026, the focused laptop check
`postgres-queue-20261001-130927` passed all 10 storage cases and all seven VM
application cases. The outer report confirms success and temporary-resource
cleanup; its `model-proof/report.json` records seven tests with no failures/errors.
Local checks also passed 76 model/deployment-helper tests and 17 existing generic
policy/HTTP tests. The sandbox HTTP check bounded selector waits in its test process
because cross-thread wakeups stalled; no application/test source was changed for
that workaround. Compose configuration and systemd syntax checks passed, with the
local Python path substituted only in the temporary unit used for syntax checking.
The local HTTPS rehearsal above now exercises real Nginx syntax and transport;
the actual host configuration still requires the installed VM.

On 4 October 2026, `https-rehearsal-20261004-003741` passed all 27 local helper
checks and all ten HTTPS rehearsal stages. Its 63 privacy/access requests checked
private files, traversal, aggregate delivery and owner/provider permissions through
real Nginx. The 520 MiB fictional source produced a 41,997,396-byte compressed ZIP.
A deliberately slow transfer was interrupted after 4,390,912 bytes and 34.25
seconds, then resumed after a fresh application restart. The complete archive
matched its original SHA-256/ETag, including manifest hashes, nine run settings
files and two distinct input bundles. Owner sessions, queued/completed jobs and
aggregate links survived restart. Physical source deletion and account revocation
blocked saved and resumed links while other retained output remained available.
Both `report.json` and `model-proof/report.json` confirm success and cleanup of
the temporary database, proxy and fixture files.

Earlier partial runs exposed rehearsal setup issues: the local parser's required
test flag, Nginx module temporary paths in a read-only container, Uvicorn's normal
SIGTERM exit, case-insensitive HTTP header lookup and the worker-only deletion
lock. These are corrected and covered locally. The test uses the existing cache,
authentication, session-secret and cookie rules; it does not change their design.
This evidence is a synthetic local transport proof, separate from the real model
and Visualiser browser proofs and from acceptance on the chosen deployment host.

Production acceptance still requires the chosen VM, its actual filesystem/proxy,
email delivery, boot/restart, interrupted transfer, backup restore and resource
measurements. Do not label template/unit tests as completed VM deployment.

The offline backup/restore proof passed all 55 focused database/deployment checks
on 3 October, across `postgres-queue-20261003-155508` and the corrected
`postgres-queue-20261003-160608` rerun. Native dump/restore, inactive gating,
interrupted recovery, exact row/file verification, existing sign-in and
owner/provider output permissions passed with fictional data and no real mail.
Temporary resources were cleaned up. See [BACKUP.md](BACKUP.md) for scope and
the additional staging-host recovery checks.

The expanded online proof passed all 122 cases in
`postgres-queue-20261003-191224`, with no failures, errors or skips and confirmed
disposable-database cleanup. It includes concurrent PostgreSQL writes, interrupted
attempt recovery, shared SingleRun saved exports/permissions, native GnuPG
round trips and scheduler/transfer fault recovery. These fictional-data checks do
not replace a recovery exercise or real SFTP/SMTP checks on the chosen host.
