<!-- (C) Copyright 2026, by Ross Richardson
Account approval, renewal reminders, bounded expiry grace and independent retention.
@author ross richardson
-->

# MultiRun account and dataset permissions

The separate [administrator interface and guide](../SIMPATHS_ONLINE_ADMIN_GUIDE.md)
provide reviewed account and per-provider-dataset changes after schema 036 is
installed and private administrator roles/password are explicitly configured.
It requires its own password plus an emailed code. Ordinary user approval never
grants administrator authority; the private CLI below remains available.

An approved email address still needs the normal emailed sign-in code. Browser
sessions expire independently of account approval. Approvals may last 1–365 days
or have no expiry. A permanent approval remains revocable. A missing expiry with
`permanent=false` is revoked; it is never interpreted as permanent access.

## Administrator commands

Run these on the VPS as the private service user from the installed model tree.
Use the actual registered address and dataset ID. The examples contain no real
addresses. An ordinary MultiRun login does not confer these administrator powers.

```bash
cd /opt/simpaths-online/SimPaths
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.account_admin --config /etc/simpaths-online/multirun.toml list
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.account_admin --config /etc/simpaths-online/multirun.toml \
  approve --email person@example.org --days 365
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.account_admin --config /etc/simpaths-online/multirun.toml \
  approve --email person@example.org --permanent
sudo -u simpaths-online /opt/simpaths-online/venv/bin/python -B \
  -m deploy.multirun.account_admin --config /etc/simpaths-online/multirun.toml \
  revoke --email person@example.org
```

`grant-dataset --email person@example.org --dataset DATASET_ID --days 90` grants
one account access to one restricted dataset. It also accepts `--permanent`.
`revoke-dataset` removes that grant. Other users and other dataset grants are
unaffected. These operations do not launch applications, migrate the database,
send emails or delete data. They require the reviewed schema already installed.
The older `vm_web approve/revoke` commands delegate to this narrow operator path;
`approve --approval-days 365` or `approve --permanent` is accepted there too.

### Public examples and private inputs

Only the trusted Quick Start importer can mark verified public training examples
as automatically available to approved accounts. No per-user dataset expiry is
needed for these examples. Their provider provenance remains attached: raw
training inputs and individual-level simulation results cannot be downloaded
through MultiRun. Experiments and aggregate results still belong to their owner.

A user's own prepared inputs follow account approval automatically. Independent
provider grants retain their own expiry. Combining datasets requires valid
permissions for every contributing ancestor; replacing or adding an uploaded
file does not remove provider restrictions. Account renewal or permanence does
not override a restricted dataset expiry. Restricted-provider Visualiser output
remains disabled until its approved disclosure rules are implemented.

## Natural expiry and explicit revocation

At ordinary account expiry, sign-in, reads, submissions, queued simulation starts
and new retry attempts are denied. An attempt admitted before expiry can finish
within the earlier of its original runtime allowance and 48 hours after that
approval expires. This smaller deadline is frozen into the independent process
supervisor at admission. Renewing an account does not silently extend an already
admitted attempt's hard deadline. Existing stopped/review work is never silently
restarted by renewal. Its ordinary retry and total-runtime budgets remain in force.

The private service may complete automatic aggregation within account grace.
This does not grant the expired user permission to read the resulting charts.
An explicit account revocation invalidates existing browser sessions and stops
running work promptly. Restricted dataset expiry/revocation also stops affected
work promptly; it has no account grace. Capacity remains charged until execution
is confirmed stopped.

Account and dataset permissions govern access, not storage lifetime. Uploaded
files, scientific outputs, cached aggregate charts and retained summaries keep
their normal retention rules after approval expires or is revoked. Revocation
does not reset or shorten their deletion clocks. The private retention reader
can verify already stored identities without granting browser or processing
access. Explicit source deletion and normal cache-pressure eviction retain their
existing effects. Renewal can restore access to remaining files, but cannot
restore files already deleted under their retention rules. There is no combined
"revoke and delete everything" action.

## Renewal reminders

Finite approvals have two notices: one calendar month before expiry and seven
days before expiry. The calendar-month calculation clamps dates at month end
(31 March becomes 28 or 29 February) and preserves the expiry's time of day.
Each message gives the exact UTC expiry and asks the recipient to contact the
configured administrator address to request renewal. No renewal is automatic.
Permanent, revoked and already expired approvals receive no renewal messages.

Notices are bound to the approval revision and expiry. Renewal, revocation or
conversion to permanent approval cancels stale pending notices. A late startup
within seven days sends only the current seven-day notice, not two catch-up
emails. A minute-spaced bounded scan records due notices, using the existing
private SMTP delivery setting. Disabled delivery records due notices but sends
nothing. No scientific data, input labels, credentials or attachments are emailed.

The outbox claims each logical notice once at a time. Failed delivery retries
with a stable Message-ID and bounded backoff. As with other SMTP notifications,
a lost relay acknowledgement can cause a repeated delivery of that same logical
message. A per-account advisory lock serializes approval changes with the final
revision check and SMTP call, without holding the scheduler's pool lock during
network delivery. Other users' jobs continue to use the pool. Completed/cancelled
notice metadata is retired after 90 days. Backups preserve approval revisions,
permanent flags, scoped grants, message IDs, delivery history and pending retries;
restore does not renew an account, send messages or activate a service.

## Qualification

The native proof uses tiny fictional files, captured email senders, synthetic
expiry dates and disposable PostgreSQL. No scientific simulations, public
services or real emails are involved.

```bash
cd ~/git/JAS-mine/JAS-mine-web
TMPDIR=/tmp-codex PIP_DEFAULT_TIMEOUT=60 \
~/simpaths-browser-tests/venv/bin/python scripts/test_batch_queue.py \
  --proof-only \
  --proof-script "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/account_lifecycle_proof.py" \
  --proof-requirements "$HOME/git/SimPathsWeb/SimPaths/deploy/multirun/requirements-backup-proof.txt" \
  --output "/tmp-codex/account-lifecycle-native-$(date +%Y%m%d-%H%M%S)"
```

Deploy schema 035 and both application repositories together. Existing source
bundles with an older schema contract cannot be used to roll back the database
after migration. Preserve the prior application and verified recovery point.
Deploying this code alone does not extend any existing user's approval: the
administrator must explicitly apply the reviewed account durations.
