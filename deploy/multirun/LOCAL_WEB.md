<!-- (C) Copyright 2026, by Ross Richardson
     Local MultiRun browser preview: approval, prepared examples and queued uploads.
     @author ross richardson -->

# Local MultiRun browser preview

This is a separate local application at **http://127.0.0.1:5002**. It uses the
tested PostgreSQL queue and container adapters. It does not start `app.py`, use
Redis, or replace the interactive SingleRun page. No image rebuild is needed.
The page uses the same SimPaths logo and favicon as SingleRun, with the logo
beside **SimPaths UK MultiRun**, including before sign-in. The assets are served
locally by JAS-mine-web.

You can sign in, select prepared training inputs or upload and prepare your own,
create fixed configuration cards, duplicate them, select a baseline, review the
shared seed sequence and submit. **My jobs** shows durable status, attempt counts,
cancellation and automatic-retry controls. A browser refresh or closure does not
cancel submitted work.

The **Default input dataset** supplies inputs to every card unless that card
selects an alternative. Each dataset contains its prepared population/donor database,
UKMOD policy schedule and parameter workbooks. To compare a different schedule,
workbook set or population, create or select a second dataset and choose it on the
comparison card. Different inputs must use the same model JAR version. Each card
has its own validated population/years; all cards share the seed sequence.

**Create input dataset** appears to the left of **New experiment**, which remains
the initially visible page. Give uploaded inputs a name before preparation. The
name is retained with the published dataset; selectors refresh automatically, including datasets still being prepared, while
preserving current choices. Previously prepared datasets
without names retain their generated descriptions. Dataset names are labels, not
content identities: two datasets can have the same name, with their IDs distinguishing
them. A disappearing permission does not silently switch a selected dataset.

The configuration cards show all model parameters supported by this web profile,
in their `SimPathsModel` declaration order, with no special prominence for saving
rate. This includes parameters without `@GUIparameter`; that annotation controls
the interactive GUI, not native MultiRun YAML assignment. The supported list is
explicit in `schema.py`, with presentation metadata in `browser_model.py`.
Population, years and the shared seed plan are configured separately. Advanced
capabilities and output settings not exposed in this browser remain controlled by
the profile; the native launcher's ability to assign a field does not establish
that it is suitable for this web workflow. YAML import/export and sweep expansion
exist in the configuration tooling, but are not yet connected to the browser form.

This preview supports 1–10 configurations. Repetitions default to a maximum of 3
per configuration; the operator can set `--max-repetitions` when starting the
service. Each configuration uses the same seed sequence, starting at 606 and
increasing by one per repetition. The local pool admits one job at a time, with two CPUs and up to
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
  --max-repetitions 3 \
  --prepared /path/to/prepared-20000 \
  --prepared /path/to/prepared-50000
```

The `--prepared` arguments are optional and remembered for subsequent starts.
Only these explicitly imported public training examples are granted to approved
local identities. Uploaded datasets remain owner-specific. The worker and browser
server run in this terminal; leave it open while testing.

### Configure the repetition limit

Set `--max-repetitions 10`, for example, to allow users to request up to ten
repetitions per configuration. The operator setting accepts integers from 1 to
1,000 and defaults to 3 if omitted. Supply it on each launch; it is not saved in
the state directory. Invalid values are rejected before connecting to PostgreSQL
or Docker. The technical ceiling of 1,000 is not a measured production allocation.

The same setting supplies the form's maximum, the New experiment description,
and server-side validation of new submissions. It applies to prepared training
data, prepared uploaded data and datasets awaiting preparation. The form initially
selects three repetitions, or the configured maximum if smaller. Restored drafts
keep their previously entered count and must satisfy the current limit on review.

Changing the setting requires a service restart and page reload. Lowering it
restricts new submissions; accepted jobs keep their frozen seed plans through
preparation, execution and recovery. Repetitions within a configuration remain
sequential. Increasing their limit does not raise the number of concurrent jobs,
the per-attempt deadline (currently one hour), the total retry budget or the
memory/storage allowances. Choose production limits alongside those budgets after
measuring representative workloads. This option requires no image rebuild.

### Configure the upload allowance

The default allowance is **2 GiB of retained source uploads per user**. If the
page reports **Upload storage allowance is full**, restart with
`--upload-allowance-gib 4`, for example, to raise that allowance to 4 GiB. Supply
the option on each launch; it is not stored in the state directory. It accepts
positive whole GiB values that fit the service's 64-bit byte counter; invalid
values are rejected before connecting to PostgreSQL or Docker. The server prints
the selected allowance at startup. No image rebuild or database reset is needed.

Keep the same `--state` directory (by default `~/simpaths-multirun-local`) to
retain approvals, uploads, prepared datasets and submitted jobs. Raising this
allowance does not allocate storage, free disk space, or change the 512 MiB
per-file limit, 100-file allowance or preparation/run space checks. Uploads still
leave at least 1 GiB free on their filesystem. Check free space where the state
directory lives; `/home` and `/` may be different filesystems. Lowering the
allowance does not delete existing uploads, but can prevent further uploads.

In **Create input dataset**, **Clear unused files** deletes only unticked sources
not retained for datasets or submitted preparations. Referenced sources stay
listed, stored and counted and can be selected again, including after a prepared
dataset is deleted. On restart, migration 009 restores previously cleared sources
where no current copy of that filename is listed; the latest retained ready version
is restored without changing the sources used by existing jobs. Automatic expiry
of those retained sources is still pending.
Reuse existing selected population/UKMOD files when creating a dataset with a
different replacement workbook. Choosing a file whose name is already listed
compares its contents using a server-calculated SHA-256 and byte count. Identical
files are selected for reuse without another stored copy, even at full allowance.
Changed files require confirmation; existing datasets and submitted preparations
keep their original versions. The comparison transfers the chosen file to the
server without saving it; a confirmed replacement is then uploaded and remains
subject to the storage allowance and free-space checks.
The list shows **Uploaded:** with each file's upload date/time and **Replaces an
earlier upload** for a confirmed replacement. A workbook uploaded then edited in
the browser keeps its upload date and adds **Updated:** for the latest saved edit.
Reusing identical contents changes neither date. A copy edited directly from a
model original has only an update date. Times use the browser's local timezone.
Do not remove files directly from the upload
directory: doing so leaves their database records and references behind.

**Console codes are an explicit local test mode.** Request a code on the page,
read it in this terminal, and enter it in the browser. This exercises the same
approval/session checks but does not prove access to an email inbox. The app
binds only to loopback. Do not expose it using a tunnel or reverse proxy.

## Suggested manual check

1. Sign in and choose the prepared 20,000-person training dataset. Its saved
   population and first year are fixed; leave the final year at 2020.
2. Leave the first configuration's saving rate at its default. Duplicate it,
   rename the copy, and change its saving rate. Duplicate cards must differ in
   at least one effective setting or input dataset before submission. Alternatively,
   select the 50,000-person training dataset on the copy; its saved population
   becomes 50,000 while the first card still inherits the 20,000-person default.
3. Select the first configuration as the baseline. Review the total simulation
   count, seeds, population/years, input identities and highlighted model differences.
   File labels compare with the baseline, or name matching configurations when no
   baseline is selected. Expand **Compare input files** above the settings table
   for the size/checksum explanation and individual changed or missing files.
   These checks identify changed files, not changed cells or database records.
   The schedule row compares the workbook; identical schedules can still refer
   to different donor data in the separately compared prepared database. Pending
   generated files say **Awaiting preparation**, with selected sources listed
   separately. You can submit immediately and wait for preparation, or wait and
   review again to compare generated files before submitting.
4. Open **My jobs**, wait for automatic updates, then close and reopen the page. Confirm that the
   same jobs remain visible. Cancellation and retry controls apply to one
   configuration, not every configuration in the experiment.
5. For the upload path, choose **Create input dataset**, enter a name, then upload a population CSV and
   UKMOD files, check the policy schedule (including a policy starting in 2015),
   then review and click **Create input dataset**. It queues preparation immediately.
   Select the named dataset in **New experiment** while it is still preparing;
   submitted configurations wait for validated inputs before becoming runnable.
   Prepared uploads can be reused in later experiments without preparing again.
6. Open **My datasets** to see only your created datasets. Cancel a pending
   preparation through its impact review, or delete a ready dataset after explicit
   confirmation. Choose replacement inputs for never-started dependent configurations
   or cancel those configurations. Running/retrying configurations retain their inputs;
   requested deletion waits until they finish or are cancelled. Source uploads,
   existing results and job history are kept separately.

Parameter workbooks must be declared model inputs. This page does not accept
input database files or archives. The native SimPaths validators still decide
whether a selection is usable. Keep local tests to public example data until
production storage controls and retention are completed.

## Waiting, replacement and deletion

Preparation runs even if no experiment depends on it. Waiting configurations retain
submission age and reserve no execution CPU/RAM. Once preparation succeeds, their
settings, seeds, input identity and resources are validated again. Normal queue
fairness and capacity determine when they start; readiness does not guarantee the
next slot. Failure blocks only configurations using those inputs.

Input replacement requires a review and is allowed only before a configuration has
started. It retains the original parameters, seeds and queue age, and records the
change. Incompatible years, population requirements or model versions are rejected.
Started work and its retries keep fixed inputs. A changed job or preparation status
invalidates an open action review; review again to see the current consequences.

Requested deletion is shown as **Awaiting deletion** while references remain.
The worker removes unreferenced prepared files from managed storage and recovers
interrupted removal on restart. Provider examples are excluded from **My datasets**.
Seven-day expiry and 96-hour warning emails are not yet implemented in this preview.

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
Restart after updating both repositories to apply migration 010 (and earlier
migrations when needed). It preserves existing jobs, registers owned prepared
inputs for management and gives unfinished preparation a selectable dataset ID.
Migration 008 records which uploaded workbooks were edited in the browser.
Migration 010 distinguishes upload and edit dates. Older edited copies show their
known update date; the old schema did not preserve their original upload date.
Do not run an older dispatcher against the upgraded schema. Stop the local
launcher before updating, then restart it normally; do not delete its database.
Recovery may wait for the previous lease to expire (up to about one minute).
Stopping the frontend is therefore not the way to cancel a job: use **Cancel**.

Within an experiment, the order on **New experiment** is the preferred starting
order when configurations are ready and fit the available resources. The baseline
is used for comparisons and does not change queue priority. Waiting configurations
keep their original position while other ready work may proceed; retries still
observe their backoff. User fairness and resource limits continue to apply, and
available capacity can run configurations in parallel. Completion order is not
guaranteed. The dispatcher rechecks waiting inputs after publishing preparation
results and before claiming further work, so a just-prepared first configuration
can be considered immediately. Restart the local launcher to load this scheduler
change; no database migration or image rebuild is required.
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
opt-out, owner isolation, pending-input dependencies, confirmed deletion and narrow layout. It does not rebuild model images or
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

## Editing replacement parameter workbooks

Use the download symbol beside an original file in **Workbooks you can replace**
to save that Excel file unchanged. Edit it locally, recalculate and save, then
upload it with exactly the same filename. The filename itself still opens the
browser editor. This also provides the editing workflow for workbooks containing
formulas, which remain read-only in the browser.

Only existing release defaults whose exact filenames appear in
`deploy/multirun/public_workbooks.py` can be viewed or downloaded. This declaration
is separate from preparation's input inventory: adding an Excel file to the input
directory does not publish it. Maintainers must review the entire workbook,
including hidden sheets, before adding a name or changing a public release
default. This is an access allowlist, not automatic detection of microdata.
Never put restricted microdata into a public default or point release defaults
at prepared/provider datasets. The browser cannot select arbitrary paths or use
these controls to download databases, population CSVs or UKMOD donor files.

Under **Create input dataset**, expand **Workbooks you can replace**. The list
comes from the frozen model release and uses the same filenames as preparation
validation. It excludes `DatabaseCountryYear.xlsx` and `EUROMODpolicySchedule.xlsx`,
which preparation generates. Click a filename to inspect or edit an original copy,
or click an uploaded replacement to continue editing that copy.

**Save replacement** selects the saved copy and shows **(updated)** and its latest
**Updated:** time, alongside **Uploaded:** if the edited copy came from an upload.
The original stays unchanged. Confirming deselection
uses the original workbook for the new dataset. A same-name upload with identical
contents reuses the edited copy; changed contents can replace it after confirmation.
Existing submitted preparations keep the exact
version they were reviewed and submitted with; editing does not change old jobs.
If another browser changes the file while you edit, saving fails instead of
silently overwriting that newer version. Close and reopen the latest copy.

This is a cell-value editor, with worksheet selection and paged rows/columns.
Numeric, text and boolean cells retain their types. Dates and formula cells are
read-only. Workbooks containing formulas can be viewed but must be changed and
recalculated in Excel, then uploaded, to avoid stale calculated values. XLSX saves
retain other workbook entry contents; advanced legacy XLS features may not survive
its SheetJS export. Workbook saves remain subject to upload space limits and the
existing isolated preparation checks. No model image rebuild is needed.
