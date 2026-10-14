<!-- (C) Copyright 2026, by Ross Richardson

Native public frontend/private executor installation and acceptance boundaries.
@author ross richardson
-->

# Separate the public frontend from privileged execution

Native MultiRun uses two Linux identities and two services. The public HTTP
application runs as `simpaths-web`. The private domain service and dispatcher run
as the existing `simpaths-online` identity, preserving ownership of retained data,
model releases, signing keys, execution receipts and quota-bound workspaces.

| Component | Access |
| --- | --- |
| `simpaths-multirun.service` / `vm_frontend.py` | Public HTTP, public installed assets and bounded Unix RPC client |
| `simpaths-executor.service` / `vm_executor.py` | PostgreSQL, SMTP, private files, approved releases, Docker and quota broker |
| `simpaths-backup.service` | Existing trusted `simpaths-online` backup workflow |
| Root quota broker | Existing narrowly bounded XFS quota operations for `simpaths-online` |

The public process receives **no database role or password**, SMTP credentials,
Docker group membership, quota-broker access or private filesystem access. The
private service independently authenticates browser session tokens, derives owners,
checks permissions and accepts only named application operations. There is no
private HTTP listener, arbitrary Python method dispatch, SQL interface or generic
Docker command operation. Public client-supplied paths, images and execution
commands are not an executor API.

The trusted service still includes model adapters, workbook handling, results,
aggregation and queue policy. It remains security-sensitive and needs reviewed
code and dependencies. This split limits a compromise of the public HTTP process;
it does not claim that a vulnerability in trusted backend code is harmless.
SingleRun has its own public gateway and private executor, implemented in
JAS-mine-web. Its public account `simpaths-singlerun-web` uses a separate socket
and cannot invoke this MultiRun RPC service. The SingleRun private application
retains its existing session authentication, PostgreSQL and Docker authority.
Follow the companion `docs/vm-deployment.md` installation instructions and verify
its separate UID/socket, browser and restart checks on the installed host; the
MultiRun acceptance results do not establish SingleRun host isolation.

## Accounts, code and configuration

Create `simpaths-rpc` as a dedicated group and `simpaths-web` as a dedicated
non-login account. Retain the existing `simpaths-online` account and existing
0700 private directories. The public account must not belong to `docker` or to
any group able to read private data. Its additional `simpaths-rpc` membership
allows connection to the private socket only. The executor unit uses this group
for its runtime directory; existing private state remains protected by UID and
0700/0600 modes, irrespective of group ownership.

Install code, the Python environment and configuration under administrator
control. Neither runtime account may write installed source, dependencies or
configuration. Both native launchers verify every file and directory in the installed application
trees and Python environment. They reject runtime-writable descendants and source
symlinks, as well as unsafe ancestors. Administrator-owned interpreter/environment
links are permitted only when their target chains are also protected. Install an
ordinary root-managed virtual environment; editable installations pointing back to
a developer home directory do not satisfy this boundary.

- `/etc/simpaths-online/multirun.toml`: private executor configuration, root-owned
  and readable only by root and the executor (for example root:`simpaths-online`
  mode 0640; keep the public account out of that group).
- `/etc/simpaths-online/public.toml`: root-owned mode 0644, copied from
  `vm/public.toml.example`; contains only origin, listener, frontend code path,
  RPC socket path and expected executor account.
- `/etc/simpaths-online/postgres.dsn`: existing mode 0600, owned by
  `simpaths-online`. SMTP secrets remain available only to the executor unit.
- `/run/simpaths-executor/control.sock`: created by the private server under its
  systemd-managed 0750 runtime directory; mode 0660 and group `simpaths-rpc`.
  The server checks the connecting UID with `SO_PEERCRED`; group membership
  alone is not sufficient. The public client verifies the executor's UID.

Ensure the public and private origins and socket paths match. Adjust the public
unit's `InaccessiblePaths` if the private storage or credential paths differ from
the templates. The frontend's mount restrictions also hide the Docker/quota
sockets and private state. Its network policy permits loopback access only.

The private `[rpc] frontend_user` defaults to `simpaths-web`. The public
`[rpc] executor_user` defaults to `simpaths-online`. Native startup rejects root
or a shared frontend/executor UID. The public startup additionally rejects Docker
group membership, accessible privileged sockets and inherited PostgreSQL/SMTP/
Docker environment settings.

PostgreSQL remains owned by the trusted runtime role with cluster-wide privileges
removed as before. Public users and the public HTTP UID receive no SQL capability.
Operator migrations/restore stay on the private side. Existing database rows and
schema permissions are not loosened to share data with the frontend.

## Startup and migration

Stop the old combined web service before installing the new units. Do not delete
state, session keys, containers, database volumes or quota ledgers. Keep the same
executor UID, state paths, installed image IDs and frozen release records.

Install both unit templates and the public configuration. The private executor
starts first; it runs existing storage/image/quota/database preflight and obtains
the original worker/maintenance fences. The public frontend then connects to the
private service and serves the existing browser routes.

```bash
sudo systemd-analyze verify /etc/systemd/system/simpaths-executor.service \
  /etc/systemd/system/simpaths-multirun.service
sudo systemctl daemon-reload
sudo systemctl enable --now simpaths-executor.service
sudo systemctl enable --now simpaths-multirun.service
sudo systemctl status simpaths-executor.service simpaths-multirun.service
```

`vm_web check/approve/revoke/status` remain trusted operator commands. The old
`vm_web serve` route is disabled by default; it is available only as an explicit
`--private-combined` diagnostic with `dedicated_storage=false`. Hosted installations
must use the two units. The local laptop launcher remains a private development
workflow and does not prove native process separation.

Restarting the public frontend does not stop the dispatcher or model containers.
Restarting the executor drains accepted RPC work and closes cache/aggregation
processors before releasing lifecycle fences. Model containers retain their own
persisted deadlines; replacement workers adopt surviving attempts using existing
leases and identity checks. Fatal dispatcher errors restart the executor unit.
The public `/healthz` reports 503 while its private service is unavailable.

## Backups, resource changes and restore

The existing quota broker continues authenticating `simpaths-online`, never
`simpaths-web`. Its ledger, project IDs and filesystem roots stay unchanged.
Automatic resource growth and retries run entirely inside the trusted service,
using the original shared pool, frozen ceilings, attempt budgets and reconciliation.

Backups run as the trusted identity and keep their existing database/file snapshot
fences. They include private session state and approved artifacts. The public
configuration contains no secrets and can be retained separately as operator
configuration. There is no new persistent public state to restore.

An inactive restore must not start either hosted unit before activation succeeds.
Restore/activation still verifies files, database, images and destination quota
bindings. Stop both MultiRun units for offline restore or release maintenance.
When SingleRun shares the affected database/state, also stop its public
`simpaths-singlerun.service` and private `simpaths-singlerun-executor.service`.
Stopping only a public frontend leaves its private service actively using the
state. Source isolation must include every affected executor and model container.
After activation, start the MultiRun executor first, then a SingleRun executor
joining that pool, and finally the public frontends. Verify existing owner
sessions and original attempts; see [backup and restore](BACKUP.md).

## Native acceptance

Run the focused configuration tests and disposable PostgreSQL VM proof first.
The VM proof now verifies that private assembly creates no HTTP application,
that its cache/Visualiser processors run and close privately, and that readiness
omits private database diagnostics. A further check exercises public HTTP through
the framed private protocol and real database, including sign-in, streamed upload,
review/submission and frontend restart while a fictional job remains active.

All 15 native runtime checks passed on 14 October 2026 in
`~/simpaths-benchmarks/vm-privilege-runtime-20261014-152917/`; the outer report
confirms disposable database cleanup. This uses a same-UID socketpair and a
fictional local executor, so it does not replace the installed-host proof below.

On the configured staging host, with the executor running, perform the read-only
Linux identity proof using the installed environment:

```bash
sudo /opt/simpaths-online/venv/bin/python \
  /opt/simpaths-online/SimPaths/deploy/multirun/privilege_probe.py \
  --public-config /etc/simpaths-online/public.toml \
  --private-config /etc/simpaths-online/multirun.toml \
  --output /srv/simpaths-boundary-proof
```

Choose a fresh output directory. This drops to the configured public UID and an
unrelated UID, verifies denial of private files/Docker/quota access, authenticates
the real private service and rejects a generic Docker operation. The unrelated
disposable child receives the socket group temporarily to prove the server rejects
its UID even when filesystem permissions permit connection; no host group
membership is changed. The probe requires a successful connection, verifies the
executor's kernel UID and accepts only closure without any RPC response as peer
rejection. Filesystem denial, timeouts, and ordinary RPC error responses are
inconclusive and fail the proof. Authorised health controls run before and after
that check. It starts no simulation, changes no account/group and sends no email. Its evidence includes
only boolean outcomes. It is a selected-host check and has not been completed by
sandbox-only tests.

The existing fictional-data browser proof can also exercise the public facade and
private RPC framing, including selected chart sections, cache reauthorisation,
permissions, sign-out, downloads and privately owned aggregation. From JAS-mine-web:

```bash
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 ~/simpaths-browser-tests/venv/bin/python \
  tests/browser/batch_visualiser.py --browser-only --private-rpc \
  --build /tmp-codex/visualiser-build-20261011-cache-b \
  --output "$HOME/simpaths-benchmarks/vm-visualiser-rpc-$(date +%Y%m%d-%H%M%S)"
```

Use the intended pinned build. This is a same-UID socketpair fixture with a
disposable database and tiny synthetic CSVs. Its report identifies the mode and
records which operations crossed the protocol. It does not exercise the installed
socket listener or prove Linux account isolation. The RPC mode passed natively on
14 October 2026 in `~/simpaths-benchmarks/vm-visualiser-rpc-20261014-155923/`.
Both reports confirm cleanup; the browser and private processors reported no
errors, and all private processor and RPC handler threads stopped. The deliberate
aggregation failure tests retry handling. Broken-pipe log messages record client
connections closed before response delivery finished.

Then exercise the actual compiled browser workflow over the installed public service:
verified sign-in, upload/review/submit, completed results, permitted downloads,
restricted-data denials, Visualiser sections/cache and logout. Test a public
frontend restart while a fictional model runs, then an executor restart, confirming
the original attempt/container and resource reservation remain unchanged. Retain
existing Docker resource-growth/retry, XFS quota and backup/restore rehearsals;
ordinary mocked executor tests do not establish host isolation or kernel limits.
