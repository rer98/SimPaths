<!-- (C) Copyright 2026, by Ross Richardson

Lightweight host filesystem capacity monitoring, administrator mail and VM setup.
@author ross richardson
-->

# Host filesystem capacity alerts

`host_capacity.py` checks each configured filesystem independently of application,
Docker and PostgreSQL availability. Its separate systemd timer runs approximately
every five minutes. No simulation worker, Java instrumentation or database is
involved. It does not resize filesystems or change scheduler allocations.

## Initial policy

| Setting | Default |
| --- | --- |
| Warning threshold | At least 85% of usable byte capacity or inode capacity |
| Recovery | Both relevant readings below 80%; unknown counters do not clear earlier warnings |
| Check frequency | Every five minutes, plus small systemd scheduling slack |
| Continued warning reminder | Once per 24 hours |
| Combined mail / failed-delivery spacing | At least 30 minutes |
| Maximum configured paths | 32 |

New warnings normally send at the next check. If another notification was recently
attempted, the shared 30-minute spacing applies to new warnings and recoveries
too. This bounds combined warning/recovery/reminder/retry attempts to about 48 per
day per instance. Healthy operation sends no routine messages. A normal first
warning and later recovery use two emails; persistent pressure receives a daily
reminder. SMTP failures retain pending state and a stable notice reference.
An uncertain SMTP acknowledgement can cause a duplicate on retry; notice IDs
identify that retry. Mail contains labels, configured mount paths and capacity
figures, without scientific records, job names, credentials or exception messages.

Expected mount points that are missing, inaccessible or otherwise unmeasurable
also warn. The monitor checks the kernel mount inventory before reading capacity,
so an unmounted backup/data volume is not reported as its healthy parent root
filesystem. Multiple configured aliases on the same filesystem share one warning.
Configuration removals are reported as changes rather than confirmed recoveries.

## Cost and resource boundaries

Each check reads one bounded `/proc/self/mountinfo` inventory and a small fixed
number of path/statvfs metadata calls per configured allocation. It does not walk
directories, read result files, calculate checksums or run `du`, Docker, LVM or
PostgreSQL commands. Byte usage is used / (used + service-available free), similar
to `df`; filesystem blocks reserved from ordinary users are excluded from that
denominator. Inode counters represent the filesystem's file-entry capacity and
can be unavailable on filesystems without meaningful inode limits.

The process exits between checks. The service template limits CPU to 10% of one
vCPU, memory to 96 MiB, tasks to 16 and normal runtime to 90 seconds, with low CPU
and I/O priority. These are ceilings, not permanent resource reservations.

Only the latest bounded status/delivery state is retained, at most 128 KiB per
state file. Its on-disk size is normally a few KiB. The journal receives the latest
numeric status; system journal retention applies. This is separate from the
application's operational history and administrator monitoring exports.
The unassigned LVM reserve has no filesystem to sample; inspect it with `vgs`.

## Configure after provisioning the real mounts

Review [vm/host-capacity.toml.example](vm/host-capacity.toml.example). It includes
root, PostgreSQL, Docker, application data, backups, boot and EFI mount points.
Match these to the actual installed layout. `require_mount = true` is the default;
using a directory inside another filesystem is only an explicit diagnostic option
with `require_mount = false`.

Install the source in the reviewed `/opt/simpaths-online/SimPaths` checkout and use
the existing venv/frontend. Create a separate unprivileged system account once:

```bash
sudo useradd --system --no-create-home --user-group \
  --home-dir /nonexistent --shell /usr/sbin/nologin simpaths-capacity
```

This account needs no Docker group, database credentials, project files or private
result-directory access. The mount paths' parents must permit metadata lookup;
keep the private data directories themselves protected. Verify these permissions
through the real service account, rather than relying only on an administrator's
root preview.

From the installed SimPaths checkout, create the private configuration and install
the units:

```bash
sudo install -o root -g root -m 0600 \
  deploy/multirun/vm/host-capacity.toml.example /etc/simpaths-online/host-capacity.toml
sudo install -o root -g root -m 0644 \
  deploy/multirun/vm/simpaths-capacity.service /etc/systemd/system/simpaths-capacity.service
sudo install -o root -g root -m 0644 \
  deploy/multirun/vm/simpaths-capacity.timer /etc/systemd/system/simpaths-capacity.timer
```

Keep populated configuration outside Git. Set the instance label, actual mounts
and administrator recipient. Initially set `[monitor] enabled = true`, leaving
`[alerts] enabled = false` to check metadata/permissions before sending messages.
Both settings default to false in the example.

The unit uses `LoadCredential` to give the monitor a private read-only copy of its
root-managed configuration. systemd loads the existing root-private SMTP
environment, `/etc/simpaths-online/smtp.env`. It reuses the existing Brevo-compatible
SMTP helper with a ten-second socket timeout. SMTP environment sharing does not
enable mail: `[alerts] enabled = true` is required separately. No SMTP key belongs
in the capacity TOML or public examples.

Preview capacity without sending mail or writing status:

```bash
sudo /opt/simpaths-online/venv/bin/python -m deploy.multirun.host_capacity check \
  --config /etc/simpaths-online/host-capacity.toml
```

Then validate the actual installed unit and run one check under its restricted
account:

```bash
sudo systemd-analyze verify /etc/systemd/system/simpaths-capacity.service \
  /etc/systemd/system/simpaths-capacity.timer
sudo systemctl daemon-reload
sudo systemctl start simpaths-capacity.service
sudo journalctl -u simpaths-capacity.service -n 100 --no-pager
```

Confirm every intended mount is available and the CPU/memory/permissions settings
are effective. After enabling `[alerts]` and loading the SMTP environment through
the approved private administrator workflow, the explicit transport test is:

```bash
/opt/simpaths-online/venv/bin/python -m deploy.multirun.host_capacity test-mail \
  --config /etc/simpaths-online/host-capacity.toml
```

Run that from an authorised account able to read the private config and with SMTP
variables already set. It sends one clearly labelled test email, with no capacity
incident, probes or state changes. It does not itself read/source an environment
file. Confirm inbox delivery, then enable the periodic timer:

```bash
sudo systemctl enable --now simpaths-capacity.timer
```

## Status, changes and recovery

```bash
sudo systemctl list-timers simpaths-capacity.timer
sudo journalctl -u simpaths-capacity.service --since today --no-pager
sudo /opt/simpaths-online/venv/bin/python -m deploy.multirun.host_capacity status \
  --config /etc/simpaths-online/host-capacity.toml \
  --state /var/lib/simpaths-capacity --runtime-state /run/simpaths-capacity
```

State is service-owned 0700, with private atomic JSON, a bounded notification
snapshot and an overlap lock. The runtime fallback on `/run` retains delivery
spacing if persistent status writes fail because the normal disk is full; it is
copied back to persistent state when that becomes writable. Verify `/run` is
RAM-backed on the installed host. Its fallback does not survive a reboot; older
persistent delivery state can consequently cause a repeat notice after recovery.
If neither state location is writable, the unit fails and records a redacted
error in the journal. Monitor that service state during host acceptance.

The configuration and SMTP environment are reread at each invocation. Threshold,
recipient and enablement changes take effect at the next check without restarting
applications or simulations. Set `[alerts] enabled = false` to retain observations
without mail, or stop/disable `simpaths-capacity.timer` to remove periodic process
startup too. No sampling is charged to scientific job timing instrumentation.

## Local validation and selected-host acceptance

Run the focused checks with the existing test interpreter:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
PYTHONDONTWRITEBYTECODE=1 ~/simpaths-browser-tests/venv/bin/python \
  -m unittest deploy.multirun.test_host_capacity -v
```

All 28 focused checks passed on 22 October 2026. They exercise real local
metadata, threshold/hysteresis/restart behavior, combined
warnings, alias deduplication, missing mounts, inode pressure, disabled controls,
private state, full-disk fallback, mail retry spacing, clock correction and the
explicit test-email path with a captured sender. No scientific simulations or
real messages are used.

The 22 October local read-only footprint probe used three fresh root-filesystem
CLI processes: median wall time about 0.062 seconds, total child CPU about 0.188
seconds, and subprocess peak RSS below 19 MiB. Evidence:
`/tmp-codex/host-capacity-local-20261022-145851/report.json`. systemd 255 verified
both staged units after substituting only the uninstalled interpreter and working
directory. This is local functional/footprint evidence, not a VPS mixed-load
benchmark. Installed mount permissions, live unit limits, actual Brevo delivery,
state persistence, fallback and reboot/timer behavior remain VM acceptance tasks.

The Linux [statvfs interface](https://man7.org/linux/man-pages/man2/statvfs.2.html)
documents these kernel capacity counters. The unit's scheduling and credential
behavior follow the [systemd timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)
and [execution environment](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)
documentation; the actual templates also passed the local systemd 255 parser.
