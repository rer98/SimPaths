<!-- (C) Copyright 2026, by Ross Richardson
Planned MultiRun updates, live-compatible controller replacement and automatic drain.
@author ross richardson
-->

# Planned application updates

The application-update coordinator closes **new MultiRun starts** while the
existing executor continues observing running work. It records a durable pending
update in PostgreSQL and can wait independently of the administrator's laptop.
This is separate from a CPU/RAM capacity fault. Ordinary cancellations, successful
completion, heartbeats and permitted resource growth continue. SingleRun is not
part of the MultiRun drain and its services are not restarted by this workflow.

The new admission fence is introduced by queue migration **038**. Installing
this machinery for the first time still needs a reviewed idle installation: an
old worker does not know the new fence. Do not set the database flag and assume
an old executable will observe it. Later compatible updates can use the installed
coordinator. The original restore/maintenance gate remains independent too.

## Select the appropriate route

`drain` waits for current model simulations, input preparation and active chart
processors, receiving uploads and destructive cleanup to finish before controller
replacement. Queued jobs stay recorded and retain their original model, inputs,
seeds, attempt history, resource policy and deadlines. New starts are fenced
under the same pool lock as queue admissions, rather than by repeatedly checking
an idle screenshot. Pending simulation-to-chart handovers have no running helper;
they remain charged and queued and do not prevent the update.

`live` permits running model containers to continue during the short controller
restart, but waits for input preparation, active chart processors and file
operations to settle. The replacement executor adopts the same containers. This
route is available only for the fixed installer's supported same-schema files
and an exact qualified candidate. A generic “compatible” checkbox is not enough.
Qualification must demonstrate running-container adoption, frozen work and
resource preservation, controller publication and rollback for the candidate.
Scientific code, runner changes and incompatible formats are not made safe by
calling a package `live`.

The fixed installer validates a bounded package manifest and fingerprints its
candidate programs and qualification evidence. Program bytes are staged outside
the active program tree. Controllers stop before bundle publication; code files
are replaced through the reviewed installer, rather than changing files under
an executing controller. The previous bytes remain available for verified
rollback. No shell command or executable module is selected from manifest text.

Incompatible database/storage changes and host operations needing a reboot or
Docker/PostgreSQL interruption still require their own maintenance procedure.
This coordinator does not perform arbitrary schema migrations or host updates.

## Start and inspect an update

First build and qualify the exact update package and stage it in the root-owned
location approved by its installer. Preserve its plan checksum. The following
commands run **on the VPS**, from `/opt/simpaths-online/SimPaths`. Replace the two
uppercase placeholder values with the reviewed package directory and plan hash.

The reviewed package has `plan.json`, `qualification.json` and only the target
programs under `programs/PROJECT/RELATIVE_PATH`. The plan binds each installed
preimage and candidate SHA-256, the complete unchanged migration list, its mode
and the qualification-receipt hash. The receipt must bind the exact targets,
record passing tests without errors/skips and successful cleanup, and provide
the running-container/attempt/controller evidence for live mode. It is prepared
and reviewed before requesting a drain; these are not flags to invent during an
installation. The live allowlist currently covers batch static assets,
administration and notification formatting. Other code uses drain mode.

Upload the data package to a private operator directory, then stage it:

```bash
sudo /opt/simpaths-online/venv/bin/python -B -m deploy.multirun.application_update \
  --config /etc/simpaths-online/multirun.toml stage \
  --source UPLOADED_PACKAGE_DIRECTORY --candidate REVIEWED_PLAN_SHA256
```

Staging copies bounded regular files without following links, checks their
fingerprints and writes a root-owned candidate under
`/opt/simpaths-online/application-updates/REVIEWED_PLAN_SHA256`. It executes no
uploaded Python or shell code. Use the returned directory for `start` below.

```bash
cd /opt/simpaths-online/SimPaths
sudo /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.application_update \
  --config /etc/simpaths-online/multirun.toml start \
  --package REVIEWED_PACKAGE_DIRECTORY --candidate REVIEWED_PLAN_SHA256
```

`start` verifies the package before requesting the drain, then launches one
fixed root Python coordinator using `systemd-run`. Its response gives the update
reference and unit name. Once that unit has started, SSH/laptop disconnection
does not stop it. Repeating a start for the same pending candidate returns the
already running unit; a different active update is refused. No model jobs are
cancelled to create an idle window.

Status is read-only:

```bash
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.application_update \
  --config /etc/simpaths-online/multirun.toml status
```

The administrator page shows the pending phase and waiting counts. The private
CLI also identifies the exact candidate and verification receipt. The systemd
journal for the returned unit provides private operator details. Keep package
paths and private journals out of user feedback. The existing maintenance page
covers the actual public-controller reload; waiting for long simulations does
not require keeping the website unavailable for the whole drain.

## Timeouts, interruption and cancellation

The default waiting allowance is 24 hours. `--wait-seconds` accepts 1–604800;
`--poll-seconds` accepts 1–60. Long waits do not extend job deadlines. A wait
timeout exits with code 2 and **leaves the drain pending**. An operator can resume
its waiter or cancel it explicitly. Losing a coordinator does not silently open
the queue. Avoid a short wrapper timeout that would repeatedly kill a useful
long-running drain.

Before installation begins, cancellation can release the update fence:

```bash
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.application_update \
  --config /etc/simpaths-online/multirun.toml cancel \
  --reference UPDATE_REFERENCE --candidate REVIEWED_PLAN_SHA256
```

An already started installation cannot be cancelled by that command. A restarted
coordinator does not replay an `installing` update: inspect the saved candidate,
program versions and private receipt. The installer saves original source bytes
and progress in the root-owned staged directory before each replacement. The
fixed recovery command validates that receipt and all original hashes, restores
the old programs, restarts only MultiRun and verifies it before resuming starts:

```bash
sudo /opt/simpaths-online/venv/bin/python -B -m deploy.multirun.application_update \
  --config /etc/simpaths-online/multirun.toml recover \
  --package /opt/simpaths-online/application-updates/REVIEWED_PLAN_SHA256 \
  --reference UPDATE_REFERENCE --candidate REVIEWED_PLAN_SHA256
```

Do not delete or edit the staged directory while its update is pending, running
or failed. A missing or altered receipt causes refusal, not a guessed rollback.
Only verification of the installed release or a verified rollback reopens the
update fence. A successful rollback is recorded as `cancelled` with
`verified_rollback`; the CLI returns code 3 so it is not mistaken for installation
of the new candidate. A failed installation or failed verification/rollback stays
paused for investigation.

Other admission controls still apply after the update fence opens. In particular,
restoring application service does not override a genuine capacity alarm, a
storage restriction, account permissions or an inactive restore gate.

## Acceptance

The coordinator tests use fictional queues and callbacks. The native proof must
also establish queue-claim races, persistence across restart/restore, charged
pending handovers, continued running-job controls and exact-container adoption.
The installed host must verify the first migration and real systemd/HTTPS
handover before this becomes the routine release path. A local passing test is
not a receipt that a VPS update happened.
