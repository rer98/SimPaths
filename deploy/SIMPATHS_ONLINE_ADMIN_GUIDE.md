<!-- (C) Copyright 2026, by Ross Richardson
Practical administration of SimPaths Online SingleRun and MultiRun.
@author ross richardson
-->

# SimPaths Online: administrator guide

Ross Richardson · 12 November 2026

This guide explains how to administer SimPaths Online, manage account permissions
and perform everyday checks. Scientific outputs are still experimental. The
current shared training inputs come from the public SimPaths repository; admitting confidential provider
data needs separate host-security and disclosure-control acceptance.

The user workflow is described in the [User Guide](SIMPATHS_ONLINE_USER_GUIDE.md).
Use the [maintainer guide](MAINTAINER_GUIDE.md) for software and model releases.
The detailed runbooks linked below remain the reference for recovery and capacity
changes. Examples use fictional email addresses; substitute the exact approved
address and dataset ID.

## 1. Access and responsibilities

| Access | Address or route | What it permits |
| --- | --- | --- |
| Ordinary MultiRun user | <https://13.140.163.1> | Emailed-code sign-in, owned experiments, permitted datasets and results |
| MultiRun administrator | <https://13.140.163.1/admin> | Separate password **and** emailed code; account and provider-dataset permission changes, service/storage status and change history |
| SingleRun administrator | <https://13.140.163.1:8443/admin> | Existing SingleRun password login and session/user controls |
| Host operator | SSH as `simpaths-admin` | Installed configuration, software, monitoring, backups, storage and recovery |

The MultiRun administrator interface must first be installed and explicitly
enabled by a host operator. An ordinary account, even one approved permanently,
does not acquire administrator powers. Administrator email addresses can be added
or removed by a host operator through the private server configuration. The
MultiRun administrator webpage can manage ordinary user access, but cannot grant
administrator privileges.
The SingleRun and MultiRun administrator passwords and sessions are separate.

Keep passwords in a password manager and recovery credentials in a separate,
secure backup location. Never send passwords, email codes, SMTP settings, database
connection strings or backup private keys in feedback messages or screenshots.

## 2. Manage MultiRun accounts in the browser

Open **MultiRun administration** and enter your administrator email and the
separate administrator password. Enter the code sent to that inbox. A sign-in
code grants no authority unless the account is active and on the private
administrator list. Sessions expire after 30 minutes; sign in again when needed.
Sign out when finished. Administrator sign-out leaves an ordinary MultiRun
session in the same browser signed in.

Under **Accounts**, search by email or part of an address. The list includes
active, expired and revoked accounts. Select **Manage** to copy an address into
the account form, or enter a new address directly.

To approve or renew an account:

1. Check the exact email address, including the domain.
2. Choose 30, 90 or 365 days, or **No expiry (revocable)**.
3. Select **Review approval / renewal**. Read the current permissions and the
   proposed change.
4. Select **Confirm change**, or **Cancel** to leave the account unchanged.

The chosen duration starts when confirmed. Renewal preserves the account's owner
identity and remaining files; it does not restart stopped jobs or replenish their
attempt/time limits. Browser sessions still have their own expiry. An approval
does not send an invitation or create a signed-in session: tell the recipient
where to sign in, and they request their own emailed code.

To remove access, enter or select the address and choose **Review revocation**.
Confirm only after checking the displayed target and consequences. Revocation
invalidates existing user and administrator sessions and stops affected work
promptly. It does **not** delete the user's data. The interface refuses revocation
of your own signed-in administrator account; another administrator or the private
operator command can do that.

Every confirmed interface change appears under **Change history**, with its
administrator, target, time and before/after values. Repeating confirmation after
a lost response reuses that same change rather than extending an approval twice.
If another administrator has changed the target since review, review it again.
History retains up to 10,000 interface changes for one year; captured history is
also included in encrypted backups. Private CLI changes are outside this browser
history, so preserve their private operator receipts separately.

### Expiry, reminders and existing work

Finite approvals have reminders one calendar month and seven days before expiry.
The messages ask users to request renewal; they do not renew an account.
Permanent approvals have no expiry reminders and remain revocable.

Natural expiry prevents sign-in, reads, new submissions, queued starts and new
attempts. An already admitted attempt can finish within the earlier of its
original deadline and 48 hours after expiry. Renewal does not extend that
attempt's frozen deadline. **Explicit revocation has no such grace period.**
Capacity stays reserved until execution is confirmed stopped.

Files keep their normal retention dates after expiry or revocation. Renewal
restores access only to files that still exist. Neither approval nor revocation
resets retention or provides a permanent archive. There is no administrator
"revoke and delete everything" button.

## 3. Manage provider dataset permissions

An account approval and a provider dataset grant are separate decisions. Use
**Provider datasets**, enter an account email, and select **Show permissions**.
Select **Manage grant** beside the intended restricted dataset, choose a duration
or no expiry, and review and confirm the grant or revocation.

The account must be active before a new grant can be issued. Each grant applies
to one user and one dataset. Different users can have different expiry dates for
the same dataset; different datasets can have different user groups. Account
renewal does not extend a separate dataset grant. A dataset grant does not keep
an expired account active.

Public training inputs are automatically usable by every approved account and
need no individual grant. They retain the provider-data boundary: MultiRun users
receive aggregate products rather than downloadable raw training inputs or
individual simulation output. A user's own uploads are private to that user and
cannot be shared through this page. Mixing uploaded and provider inputs does not
remove the provider restrictions; all contributing permissions must remain valid.

Dataset expiry or explicit revocation stops affected work promptly, without the
account-expiry grace period. Other dataset grants are unaffected. Existing files
keep their normal retention dates.

A grant permits use of data; it is not approval to release all derived outputs.
Restricted-provider Visualiser output remains disabled until the dataset's
disclosure rules are approved and implemented. Do not use account or dataset
permissions to bypass this separate release condition.

## 4. Check service health and demand

**Service status** shows account totals, job states, held model/processing/SingleRun
reservations, and the current CPU, RAM and job-storage budgets. It is read-only
and refreshes every 30 seconds while open. "Available to batch" respects protected
interactive and processing allowances; it is not the host's instantaneous idle
CPU or free RAM. Job storage is a scheduler allowance, **not a measurement of how
full the filesystem is**.

Each configuration is counted in one current job state. **Review** means a
stopped or failed configuration needs attention; it is a different configuration
from one counted as **active**. Output validation can require review after the
model stops, but waiting for Visualiser charts alone does not put a simulation
into review. Chart preparation has its own status in the user's Results page.

With the capacity-reporting update installed, this page also shows the last
CPU/RAM capacity-check time, its freshness and recent capacity incidents. These
checks normally run every five minutes; readings older than 15 minutes are
marked stale. The page's 30-second refresh does not itself take a new capacity
reading. Compare detected capacity with the approved pool **plus** the service
headroom, rather than with current usage. Failed readings are shown as unknown,
with the last successful reading separately identified.

If you receive **host capacity needs attention**, use the incident reference to
find the cause, first/last failure and recovery time on this page. A valid
subsequent check can restore admissions automatically, so a healthy page an hour
later does not mean the original warning was incorrect. A recovery email follows
a warning that was sent; repeated failures within the same incident are grouped.
A later separate recurrence gets a new incident reference. Recent resolved
incidents remain visible. No restart or budget increase should be inferred from
the warning alone. See [capacity controls](multirun/COMPUTE_CAPACITY.md).

For a fuller check, connect from an authorized laptop:

```bash
ssh -i "$HOME/.ssh/simpaths_contabo_ed25519" simpaths-admin@13.140.163.1
```

Then, in the VPS terminal:

```bash
cd /opt/simpaths-online/SimPaths
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.vm_web status --config /etc/simpaths-online/multirun.toml
```

This read-only command does not migrate, launch jobs or send emails. Its private
JSON form adds `--json`. It includes queue history, cleanup, resource accounting,
retained releases and monitoring status. See [operator status](multirun/OPERATOR.md)
for field meanings and unavailable/stale readings.

Useful host checks are:

```bash
sudo systemctl status simpaths-executor simpaths-multirun \
  simpaths-singlerun-executor simpaths-singlerun --no-pager
df -hT
df -i
sudo systemctl list-timers --all --no-pager
```

Keep journals and JSON exports private. Inspect a relevant unit's recent journal
with `sudo journalctl -u UNIT_NAME --since "1 hour ago" --no-pager`. A running
Python service alone does not prove that sign-in, dispatch, charts or mail work;
check the affected workflow too.

### Filesystem storage

Open **Filesystem storage** for a read-only view of actual disk occupancy,
file footprints, cache allowances and admission commitments. The page refreshes
every 30 seconds while visible; **Refresh storage** requests the latest report.
It does not delete files, change allowances, start backups or restart work.

**Physical filesystems** shows total capacity, actual used space, available space
and percentage used. Areas on the same filesystem share one row and one pool of
free space. Available space excludes blocks reserved by the filesystem. Backup
storage can be on a separate filesystem, which can become full independently of
the job and Visualiser filesystem.

**File footprints and cache allowances** reports allocated file blocks for job
workspaces, uploaded/generated prepared inputs, Visualiser data, prepared downloads and
backups. Job workspaces include raw outputs, copied working inputs and temporary
files; their footprint is not a measurement of raw CSV files alone. Visualiser
summaries are included in the Visualiser total, so do not add those two rows.
The configured summary allowance is also inside the total Visualiser allowance,
not extra capacity. These categories do not account for every file or all
filesystem overhead.
The generated-prepared-input category covers datasets produced by this service.
Imported training or provider inputs stored elsewhere are not separately counted
in that category; reported filesystem totals include all files on those filesystems.

Footprint measurements run in a bounded background check and are cached for up
to five minutes. The first reading can say **Measuring**, and a check that cannot
finish reports **Unavailable** rather than a partial total. Refresh does not
force a fresh scan before the cache expires. Each measurement has its own time;
readings older than 15 minutes are marked stale. Unknown or stale measurements
are not proof of an empty disk.

**Scheduler and protected space** compares the job-storage pool allowance, its
unreserved allowance available to batch, and actual free space on the job
filesystem. A new configuration must satisfy both scheduler and physical-space
checks. Outstanding unwritten workspace commitments and workspace/Visualiser
safety and processing reserves are shown separately: these are promises and
protected headroom, not files already occupying disk. Completing work may release
unused commitments while its retained outputs continue using space. A storage
headroom alert compares commitments and buffers with free space; its threshold
is not the percentage of the disk occupied by files.

**Scheduled backups** shows the last successful backup, whether it is overdue,
the current capture stage, whether an external destination is configured and
the configured minimum free space. Backup file occupancy is separate from this
completion status: a large or growing file does not prove a verified recovery
copy. Unavailable status needs the private operator check described below.

### Monitoring and email

The installed performance monitoring samples every 15 seconds. Detailed model
measurements and exports support resource tuning; lightweight host/service data
also reveals outages, background load and storage pressure when no models run.
Missing measurements are not proof of zero use. Review sampled peaks alongside
failure receipts: a brief burst can occur between samples.

The configured administrator performance record is sent periodically and can
arrive as several parts. Attachments contain numeric operational history, not
raw scientific data or user addresses. Parts contain changes to cumulative
records; their totals must not simply be added together. Save all parts and use
the [performance export importer](multirun/MONITORING_EXPORTS.md) to deduplicate
and check missing parts. A test email proves that one delivery worked, not that
later exports were delivered. Check the last successful send and pending/error
state in the operator report.

The independent [host capacity monitor](multirun/HOST_CAPACITY.md) checks mounted
filesystems and inodes about every five minutes. It warns at 85% and clears below
80%, including for root, PostgreSQL, Docker, private data and backups. Treat a
missing mount as a fault, not an empty disk. Do not delete Docker volumes or
application directories to silence an alert.

### Pending application updates

After the update coordinator is installed, **Service status → Application
updates** shows a pending update, its phase and the work it is waiting for.
During a drain, current simulations continue and users can submit experiments
using available inputs; new MultiRun starts and new upload streams wait. A
separate message explains this in the user workspace. SingleRun is unaffected.

Qualified live updates can preserve running simulation containers while the
MultiRun controllers reload. Other updates wait for affected work to finish.
The maintenance page appears during the actual reload, rather than the whole
waiting period. The server coordinator continues after an SSH disconnection.

A failed update keeps starts paused until verified recovery or rollback. A
timeout also leaves the drain pending; it does not cancel users' jobs or reopen
the queue automatically. A host operator can cancel before installation starts.
See [application updates](multirun/APPLICATION_UPDATES.md) for the exact operator
commands. Database-format changes and host reboots still need a separate reviewed
maintenance procedure. Initial installation of the coordinator needs one idle
MultiRun window; the feature is not active merely because this guide is updated.

## 5. SingleRun administration

Use the existing SingleRun administrator page for its active sessions, session
termination and SingleRun-specific user controls. These controls do not approve
a MultiRun account or grant a MultiRun dataset. The current service admits one
interactive session at a time; users can request an availability email when busy.
That email makes no reservation. Explicit **Leave** gives a five-minute
notification delay; natural expiry can notify after cleanup finishes.

Ending a SingleRun session stops that user's interactive model. The user may see
a disconnected-engine message until returning to the catalogue. Prefer normal
**Leave** or the session control over killing Docker containers directly. The
protected resource allowance is released only after cleanup is confirmed.

Changes to the number of interactive sessions, their RAM allowance or protected
CPU need a reviewed shared-pool policy change. They can reduce MultiRun capacity
unless the host is upgraded. Do not simply start a second unaccounted instance.
Availability demand reports help determine whether more interactive capacity is
needed; see `docs/singlerun-availability.md` in JAS-mine-web.

## 6. Retention and backups

The current ordinary MultiRun output retention is seven days after successful
completion. Uploads, prepared inputs, aggregate charts and reusable summaries
have their own lifetimes and dependency rules. Charts/summaries currently have
separate seven-day settings, and eligible charts unused for 72 hours can be
reclaimed under storage pressure. Check the dates shown to each user and the
operator report; do not assume every artifact expires with its raw output.
See [the user-facing retention policy](SIMPATHS_ONLINE_USER_GUIDE.md#6-keep-results-manage-access-and-report-problems) and
[Visualiser retention](multirun/VISUALISER_RETENTION.md).

Owners can free space under **Storage → Output Files** using **Delete raw outputs,
keep Visualiser data**. This requires complete, compatible summaries for every
repetition; a ready chart alone is insufficient. If summaries are missing, prepare
the charts first. The review states the permanent loss of raw files and the
existing summary deadline. Confirmation preserves available charts and permits
later baseline comparisons while the required summaries remain valid; it extends
neither summary nor chart retention. New calculations needing the deleted raw
files will no longer be possible. Check the resulting Storage status and usage.

Owners can also opt into **Automatically delete raw output files after Visualiser
summaries are protected** beside the baseline choice when submitting an experiment.
It starts **off**, applies initially to each new configuration, and is shown in
the submission review. Existing experiments remain off unless their owner enables
the setting. **My Jobs** provides an independent checkbox per configuration until
the worker starts removing its files. Enabling an already eligible configuration
can cause prompt, permanent raw deletion; the interface asks for confirmation.
After removal starts, opting out cannot restore the files.

Automatic removal requires a successful, validated attempt and complete, verified,
protected per-seed summaries. It can proceed while companion configurations remain
unfinished and before the final comparison publication, because compatible future
comparisons can use those summaries. It preserves input datasets, job history and
the existing finite summary/chart deadlines. Missing, expired or unprotected
summaries retain raw output for attention rather than allowing a full-output
expiry to bypass the protection check. The option does not itself create room
for summary processing; a filesystem shortage can still need operator action.

Ordinary raw-output expiry reminders are suppressed for successful configurations
using this policy, including unsent reminders queued before the change. Completion
messages state the current policy and distinguish raw-only removal from full
result deletion. Older emails already delivered cannot be recalled; users should
check current status. Turning the setting off before removal starts restores
ordinary retention with the existing minimum 96-hour warning protection where
needed. Input, upload, recovery and failed-partial-output policies are unaffected.

The separate **Delete output files** action also withdraws hosted aggregate access.
After raw-only deletion, this control is named **Delete results and Visualiser
data**. Neither action recalls an aggregate ZIP already downloaded by a user.
The administrator webpage has no user-data deletion control; operators should
follow the reviewed lifecycle procedures rather than removing files directly.

Daily encrypted local backups and hourly retry checks are configured. The complete
scheduled invocation has a **six-hour limit**, covering source-byte verification,
database export, compression/encryption and ciphertext checks. This is a maximum,
not an expected duration. A failed or unfinished capture does not replace the
previous verified backup. Check completion status rather than treating a growing
temporary file as a recovery copy.

The approved local rotation policy keeps **two completed full backups**. Its
settings are `keep_latest=2` and `retention_rule="count"`. Each full archive is an
independent recovery point. This rule removes older completed scheduled copies
without a 30-day minimum age. The runner verifies both retained archives against
their ciphertext checksums and matching receipts before retiring older copies;
a missing or corrupt retained copy stops retirement. The pending capture is
protected. A durable journal and quarantine outside the active job directories
allow interrupted retirement to resume safely. Separate migration checkpoints
and host-settings copies are outside this scheduled-copy rotation.

Leave room for the replacement archive as well as the two completed copies,
capture scratch, other recovery files and the configured free-space reserve.
Backup sizes vary with retained inputs and results; keeping two does not guarantee
that every future replacement fits. A free-space failure leaves protection
overdue until a complete new recovery point passes its checks. Check the backup
filesystem and current incident; deleting live raw output does not shrink an
existing encrypted backup. Backup failures do not mark chart preparations failed.

A separate, small encrypted host-configuration copy preserves settings such as
stable session secrets. Refresh it after configuration changes, including backup
rotation and the finite backup/chart runtime limits. Online application
backups retain settled files and database state; they are not a checkpoint of the
Java engine's current in-memory state. SingleRun controls can be briefly protected
while its recovery state is captured. An interrupted model is recovered or retried
within its existing budgets.

The recovery private key and its passphrase stay outside the VPS. Keep the
verified private-key export and key metadata in a separate secure location, along
with downloaded encrypted recovery copies. Losing both the key and its usable
export makes encrypted backups unrecoverable. A copy on the same VPS does not
protect against loss of that VPS: an external backup destination remains to be
configured. Keep the present local-retention policy until remote transfer,
readback and isolated restoration have passed.

The administrator password **hash**, audit and permission state are included in
application backups; the private administrator email list is in the
host-configuration copy. Restored administrator sessions keep their
original expiry. Restore does not extend approval dates, send mail or start
applications. Review the restored policy and invalidate administrator sessions
before reopening a recovered service when appropriate.

Use the [backup runbook](multirun/BACKUP.md) for status, capture, verification and
inactive restore. Prove a restore using an isolated target, never over the running
service. A "scheduled backup completed" recovery email can close a previous
failed-backup incident; inspect the scheduler receipt to identify the capture.

## 7. Capacity, jobs and software changes

The scheduler shares work fairly between users and then uses available resources;
it is not a promise of strict global first-in-first-out execution. CPU, RAM,
storage, preparation dependencies and per-user limits can all delay admission.
Resource settings and seeds are frozen when work is accepted. Small and large
configurations use the qualified resource policy selected for their model release.
Spare CPU may boost running models and can be reclaimed for interactive work or
Visualiser preparation. Emergency growth/retries remain bounded by the original
attempt, runtime and storage ceilings.

After **all repetitions** finish and Java stops, **Validating outputs** uses the
retained CPU/RAM allocation in an enforced helper: one worker below two currently
allocated vCPUs, two at two or more. CPU recalls apply before capacity is reused.

Validation's separate **three-hour maximum** deadline survives controller recovery.
The original model deadline remains unchanged; Java must have finished within it.
Checks do not establish scientific validity or grow memory. Success precedes the
atomic handover to separate chart preparation.

**Output validation needs review** suppresses scientific Retry; Java is not
automatically rerun. Failed-validation raw output is protected from scratch
cleanup for **seven days after validation fails**. Only a trusted operator can
request validation-only recovery after proving termination, retained files,
permissions and resources; the prior failure stays in audit history. Expiry,
explicit deletion or legacy cleanup may already have removed files. Reopening
cannot recover deleted raw output. See
[output validation](multirun/OUTPUT_VALIDATION.md).

Each chart preparation currently has a **three-hour limit**, including source
verification, aggregation, baseline comparisons and publication. Large outputs
may need substantial time; this allowance is not a completion guarantee or an
estimate of the wait. A chart-only retry reuses compatible retained per-repetition
summaries and does not rerun simulations. CPU/RAM allowances, access checks and
retention dates are unchanged by the longer time limit. See
[Visualiser preparation](multirun/VISUALISER_PREPARATION.md).

An upgraded host does not silently approve a larger shared CPU/RAM budget.
Detection records spare capacity and can notify the administrator. Inspect its
proposal, protected allowances and outside-pool headroom, then explicitly approve
the increase through the [compute capacity controls](multirun/COMPUTE_CAPACITY.md).
Accepted jobs retain their frozen settings. The
[storage budget controls](multirun/STORAGE_BUDGET.md) separately follow approved
filesystem growth while preserving free-space headroom. Growing an XFS logical
volume can be done online; shrinking it requires a planned migration. A provider
upgrade can itself require a reboot, which interrupts running processes.

For updates, stage and qualify a matching frontend/model-adapter pair and preserve
a verified recovery point. Use the tested reload procedure for the affected
components. Schema changes need an idle MultiRun window and compatible code;
maintenance responses explain an interruption but do not eliminate it. A private
executor restart must preserve/reconcile running containers and reservations; do
not replace it with ad hoc container stops or database edits. Public frontend
changes can have a narrower reload scope. SingleRun should be preserved when the
change does not require its restart.

New model versions are registered as immutable releases. Changing a default does
not replace the version of an existing prepared dataset, accepted job or retry.
Retain any old images and files still referenced by work or recovery records.
Use [release management](multirun/RELEASES.md) and the
[maintainer guide](MAINTAINER_GUIDE.md); do not overwrite frozen scientific artifacts.

## 8. Private operator controls and administrator recovery

Browser administration deliberately omits raw file access, user-data deletion,
model dispatch, pool resizing and backup restoration. The host operator retains
the narrow command-line controls. For example, on the VPS:

```bash
cd /opt/simpaths-online/SimPaths
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.account_admin --config /etc/simpaths-online/multirun.toml \
  approve --email person@example.org --days 365
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.account_admin --config /etc/simpaths-online/multirun.toml \
  revoke --email person@example.org
```

`approve --permanent` gives revocable access without expiry. `list` lists accounts;
`grant-dataset` and `revoke-dataset` accept `--email` and `--dataset`. These commands
do not turn a user into an administrator, send mail or delete data. See
[account permissions](multirun/ACCOUNT_ACCESS.md).

Administrator roles are set in the private file
`/etc/simpaths-online/multirun.toml`. The host operator edits it using the reviewed
configuration/reload procedure:

```toml
[administration]
enabled = true
emails = ["first-admin@example.org", "second-admin@example.org"]
```

Addresses must be exact, lowercase and already approved ordinary accounts.
No password belongs in TOML or source control. With the private MultiRun executor
stopped in a planned maintenance window, create or rotate the credential through
the private interactive prompt:

```bash
cd /opt/simpaths-online/SimPaths
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.administrator_config password \
  --config /etc/simpaths-online/multirun.toml
```

Use 16–256 characters. Enter it twice at the hidden prompts; never put it on a
command line or pipe it into a script. The file is service-owned mode 0600 at
`state/administrator-password.json`, containing a salted scrypt hash. It is not
the SingleRun `ADMIN_PASSWORD`. The helper refuses to change it while the private
executor is using the state. Restart through the qualified procedure afterwards.
Changing the credential or administrator list invalidates old challenges and
administrator sessions when the new configuration is loaded.

To invalidate administrator sessions immediately without changing ordinary user
sessions or restarting jobs:

```bash
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.administrator_config invalidate-sessions \
  --config /etc/simpaths-online/multirun.toml
```

Removing an email from the administrator list requires the private service to
reload that configuration. For immediate loss of all access, revoke its ordinary
account instead; that also stops its work. Restore a mistaken revocation by
explicitly approving the same address again. Old sessions remain invalid and
stopped jobs require the normal reviewed retry workflow.

If a problem is unclear, first save its time, affected job/reference and private
status/journal evidence. Check whether an update, backup, expiry or capacity alert
explains it. Avoid repeated submissions, broad Docker pruning, manual SQL fixes
and deleting files behind the retention system. Ask the maintainer to review the
evidence before performing a destructive recovery.
