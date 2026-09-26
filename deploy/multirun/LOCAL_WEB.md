<!-- (C) Copyright 2026, by Ross Richardson
     Local MultiRun browser preview: approval, prepared examples and queued uploads.
     @author ross richardson -->

# Local MultiRun browser preview

This is a separate local application at **http://127.0.0.1:5002**. It uses the
tested PostgreSQL queue and container adapters. It does not start `app.py`, use
Redis, or replace the interactive SingleRun page. No image rebuild is needed.

You can sign in, select prepared training inputs or upload and prepare your own,
create fixed configuration cards, duplicate them, select a baseline, review the
shared seed sequence and submit. **My jobs** shows durable status, attempt counts,
cancellation and automatic-retry controls. A browser refresh or closure does not
cancel submitted work.

All cards in an experiment currently share the chosen prepared dataset and policy
schedule; the cards vary the exposed model parameters. Selecting different policy
schedules or workbook overrides per card is not part of this first browser slice.

This preview supports 1–10 configurations and 1–3 repetitions each, using seeds
606, 607, 608. The local pool admits one job at a time, with two CPUs and up to
5 GiB container memory. Its allowance does not coordinate with the separate
SingleRun server: finish other simulation sessions before this local test.

## Prerequisites

- The JAS-mine-web checkout containing `jasmine_web.batch.browser`.
- Docker, the already installed `postgres:17-alpine` image and the validated
  `simpaths-interactive:uk-user-data` image. The launcher does not pull images.
- Java/Javac 25 for input preparation; the current built `multirun.jar`.
- A Python environment with the optional dependencies below and this model's
  `deploy/multirun/requirements.txt`.
- Space for one active job's input copies and retained results. The existing
  preparation and run-space guards still apply. The local launcher removes
  completed input copies, but retains scientific outputs and private diagnostics.

Install into your chosen environment, using the location of your frontend:

```bash
python -m pip install \
  -r /path/to/JAS-mine-web/requirements-batch-web.txt \
  -r deploy/multirun/requirements.txt
```

## Approve a local test identity

From the SimPaths repository:

```bash
python -m deploy.multirun.local_web approve --frontend /path/to/JAS-mine-web
```

The command prompts for an email address. Approval lasts 30 days; repeating this
explicit command renews it. `revoke` instead of `approve` removes access and
invalidates existing sessions. Neither command sends email.

## Start the page and worker

Use the **verified imports** created by `deploy.multirun.import_quickstart`, not
Docker build contexts. These directories are verified and referenced in place;
keep them available for later runs.

```bash
python -m deploy.multirun.local_web serve \
  --frontend /path/to/JAS-mine-web \
  --console-codes \
  --prepared /path/to/prepared-20000 \
  --prepared /path/to/prepared-50000
```

The `--prepared` arguments are optional and remembered for subsequent starts.
Only these explicitly imported public training examples are granted to approved
local identities. Uploaded datasets remain owner-specific. The worker and browser
server run in this terminal; leave it open while testing.

**Console codes are an explicit local test mode.** Request a code on the page,
read it in this terminal, and enter it in the browser. This exercises the same
approval/session checks but does not prove access to an email inbox. The app
binds only to loopback. Do not expose it using a tunnel or reverse proxy.

## Suggested manual check

1. Sign in and choose the prepared 20,000-person training dataset. Its saved
   population and first year are fixed; leave the final year at 2020.
2. Leave the first configuration's saving rate at its default. Duplicate it,
   rename the copy, and change its saving rate. Duplicate cards must differ in
   at least one effective setting before submission.
3. Select the first configuration as the baseline. Review the total simulation
   count, seeds, common fields and highlighted differences. Submit.
4. Open **My jobs**, refresh, then close and reopen the page. Confirm that the
   same jobs remain visible. Cancellation and retry controls apply to one
   configuration, not every configuration in the experiment.
5. For the upload path, choose **Prepare inputs**. Upload a population CSV and
   UKMOD files, check the policy schedule (including a policy starting in 2015),
   then review and submit preparation. On completion, use **Use prepared dataset**.
   Prepared uploads can be reused in later experiments without preparing again.

Parameter workbooks must be declared model inputs. This page does not accept
input database files or archives. The native SimPaths validators still decide
whether a selection is usable. Keep local tests to public example data until
production storage controls and retention are completed.

## Persistence and restart

The default private state directory is `~/simpaths-multirun-local`; use `--state`
consistently to choose another location. It stores the stable session secret,
database credential, a frozen model/workbook snapshot, private uploads, prepared
artifacts, execution receipts and scientific outputs. PostgreSQL uses its own
labelled named Docker volume, with a loopback-only port. Nothing is broadly pruned.
The launcher prints its database container name; it can be stopped after testing,
and the launcher starts it again next time.

Ctrl+C stops the browser server and dispatcher. Already launched containers keep
their independent deadlines; queued work resumes after the launcher restarts.
Recovery may wait for the previous lease to expire (up to about one minute).
Stopping the frontend is therefore not the way to cancel a job: use **Cancel**.
Do not remove its state, imported datasets or Docker volume while work is active.

The first start freezes the model JAR and default workbooks for uploaded-data
preparation. Restarting does not silently replace that release from a changed
checkout. Release upgrades and state retirement are separate operator tasks.

## Acceptance

From JAS-mine-web, using a Python with the installed Playwright browser:

```bash
python tests/browser/batch_workflow.py
```

This runs the PostgreSQL/Docker regressions in a disposable database, then a
real browser against tiny synthetic jobs. It checks grouped uploads, signed
review/submission, shared seeds, baseline, page reopening, cancellation, retry
opt-out, owner isolation and narrow layout. It does not rebuild model images or
repeat the full native SimPaths preparation proof. Reports are retained under
`~/simpaths-benchmarks/`; its temporary database and model files are removed.
If the queue suite passed and only a browser check needs repeating, add
`--browser-only` to skip rerunning that unchanged suite. Its report explicitly
records that the queue tests were skipped.

## Still to integrate

SMTP delivery, production approval administration, shared SingleRun/MultiRun
admission, hard filesystem quotas, retained-artifact accounting and expiry,
result downloads and Policy Impacts Visualiser integration remain separate work.
The UI currently reports completion, not downloadable/visualised results. Provider
microdata is never exposed by these routes. The previously reported model
receipt-flag reproducibility issue remains with the SimPaths maintainers.
