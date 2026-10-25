<!-- (C) Copyright 2026, by Ross Richardson
Concise user guide to the SingleRun and MultiRun SimPaths Online preview.
@author ross richardson
-->

# SimPaths Online: user guide

Ross Richardson · 25 October 2026

**CeMPA group preview**

SimPaths Online runs the [UK model](https://simpaths.org/) on a remote server,
through your browser. SingleRun brings the JAS-mine interface to the web, with
interactive charts, input editing and an AI assistant. MultiRun queues experiments
and runs configurations in parallel when resources permit.

| Service | Temporary address | Best for |
|:-----------------|:------------------------------------|:-----------------------------------------------|
| **SingleRun** | <https://13.140.163.1:8443> | Exploring one simulation interactively |
| **MultiRun** | <https://13.140.163.1> | Repeated simulations and policy comparisons |

Use **https://**, including **:8443** for SingleRun. No local SimPaths installation
or SSH tunnel is needed.

> **This is an experimental alpha preview.** Please explore the services, push
> their limits and report problems. Experiments may fail, outputs may be incomplete,
> and access may be interrupted. Do not rely on work finishing or remaining
> recoverable. Keep important settings, inputs and downloaded results separately.

The supplied datasets are the **public training data in the SimPaths GitHub
repository**, including tax/benefit donors. They are for learning and testing;
results are **not suitable for substantive research analysis**. You can upload
your own inputs, but please use only public or other non-confidential test data
during this preview. See the [training-data guidance](https://simpaths.org/getting-started/data/).

SingleRun is public and currently allows **one user at a time**. MultiRun requires
an approved email address and a verification code; no account password is needed.
Most server capacity is assigned to MultiRun, which can currently run up to six
configurations at once, subject to their resource requirements.

MultiRun shares capacity fairly: users without an active job receive priority;
otherwise older eligible jobs are considered first, subject to available resources
and per-user limits. Running jobs are not interrupted to admit another user's work.

## 1. MultiRun: submit your first experiment

1. **Sign in with your approved email address** and the six-digit code sent to
   that inbox. Check spam or junk if it has not arrived. Contact the administration
   if your address needs approval, and keep cookies enabled.
2. Open **New Experiment** and choose **UK training — 20,000 people** as the
   **Default input dataset**. The **50,000 people** example is also available to
   every approved user. Neither requires uploads or UKMOD setup.
3. Keep or edit the suggested **Experiment name** (`Policy Comparison N`). For
   these fixed examples, `popSize` (population size) and `startYear` (first year)
   are fixed by the dataset. `endYear` (last year) defaults to **2026**; choose
   **2020** and **one repetition** for a quick first test.
4. Set **Repetitions per configuration**. A configuration is one scenario with
   its own inputs and settings; repetitions differ by random seed. All configurations
   share the seed sequence, starting at `randomSeedIfFixed` (default **606**):
   **606, 607, 608, …**. Two configurations × three repetitions means **six
   simulations**. Repetitions run sequentially within each configuration;
   configurations can run alongside one another.
5. Name and edit the first configuration. Use **Duplicate** or **Add another
   configuration** to create alternatives, then change their settings as required.
6. Choose a reference under **Baseline for later comparisons**, or leave it unset.
   Choosing one now lets comparisons prepare automatically as configurations finish.
   You can choose or change the visualisation baseline later without rerunning
   simulations.
7. Click **Review experiment**. Check the highlighted differences, inputs, seeds,
   simulation count and runtime/storage allowances. Use **Back to edit** for
   corrections. Nothing runs yet.
8. Click **Submit to queue**, then open **My Jobs**. Jobs continue after you close
   the browser or sign out; sign in again to return to them.

**For larger tests**, choose **UK training — configurable population**. MultiRun
supports `popSize` up to **100,000** and `endYear` up to **2070**. Its current
limits are **100 configurations** and **10 repetitions per configuration**, hence
**1,000 planned simulations per experiment**. Admission also depends on queue
allowances and storage; these limits do not guarantee every combination will succeed.

### While work is running

**My Jobs** shows queued, running, completed and unsuccessful work. A configuration
may wait for input preparation, server resources, storage or a retry. Read its
status before resubmitting. Automatic recovery may increase resources or retry
an eligible failure within the recorded limits and available capacity; success
is not guaranteed.

**Cancel** requests that unfinished work stop. Cancelled jobs are shown by default;
**Show cancelled jobs** remembers your choice. **Review retry** for a failed
configuration, or **Retry** for a cancelled one, opens a review. **Confirm retry**
queues it again from the beginning with its saved settings and seeds.
Cancellation/retry history is retained. Retries share the original attempt and
runtime limits: cancelling after an attempt is created spends that attempt and
its elapsed time; cancelling before a new attempt starts spends no additional attempt.

**View configurations** compares saved parameters, inputs and seeds, with
differences highlighted. The original submitted baseline appears first, and later
additions are included. **Copy to New Experiment** creates an editable draft.
**Add Configurations** extends the existing experiment without rerunning completed
configurations, keeping its model version and seed plan. Its review includes the
original baseline as a reference when one was specified.

Completion and problem emails supplement **My Jobs**; check the website for
current status. Emails do not contain your simulation data.

## 2. MultiRun: see your aggregate results in the Visualiser

Results open in the [SimPaths Policy Impacts Visualiser](https://github.com/rjgreen1029/SimPaths-Policy-Impacts-Visualiser),
developed by Reese Green at the University of Glasgow. SimPaths Online aggregates
simulation outputs on the server before sending chart data to the Visualiser.
For training/provider datasets, users receive aggregate results without access to
the underlying microdata. Downloads from your own inputs are described in section 3.

1. In **My Jobs**, choose **View Results**. You can view ready charts while other
   configurations are still running or have failed.
2. Keep the existing **Baseline for visualisation**, choose a completed
   configuration, or select **No baseline — view levels only**. This changes
   the visualisation reference, not the submitted experiment, and reruns no
   simulations. Selecting a baseline alone starts no calculations.
3. Click **Prepare comparisons** to request the calculations in advance, or
   **Open Visualiser** to request them and open available charts. Level charts can
   be viewed while comparisons are being prepared; you do not need to wait for
   every comparison to finish.
4. Choose outcomes, population groups and scenarios. Scenario buttons show/hide
   alternatives; hover over charts to see values. **Connect Data** identifies the
   source and baseline. New charts and comparisons appear automatically in the
   same view; select newly available alternatives as required.

Initial aggregation can take minutes or longer for large outputs or a busy server.
Changing the baseline reuses retained per-run summaries where available, rather
than processing the raw outputs again. Earlier comparisons are cached separately
and can be reused within permission, retention and storage limits.

Differences use **matching random seeds** between baseline and alternative.
Variation in the paired differences across repetitions informs the uncertainty
estimates; one repetition cannot estimate that variation. After your first small
test, try three or more repetitions.

### Save or reopen charts

**Download comparison data** in **Connect Data** saves an **aggregate chart-data
ZIP** containing all configurations ready when downloading starts, including hidden
ones. Extract it, choose **Open saved comparison**, and select the folder containing
`manifest.json`. This is a fixed snapshot: it gains no later completions. Chart
export controls are also available. Opening local folders needs a supported
directory picker; Chromium has been tested. Viewing online charts does not
require that picker.

**Visualise Locally Saved Data** processes compatible raw microdata CSV folders
on your computer. Use **Getting Started → View folder structure** to organise
Baseline and Scenario folders. Local files are processed entirely in your browser
and are not uploaded. **View Online Results** returns from saved/local data to the
online experiment. For stale online charts, try **Troubleshooting → Reload charts
from server**; server results and downloaded copies stay unchanged.

## 3. MultiRun: inputs, downloads and useful shortcuts

| Page or control | What it does |
|:--------------------------|:--------------------------------------------------------------------------|
| **Create Input Dataset** | Upload compatible population CSV and UKMOD donor files; choose the model release, any replacement workbooks, and policy schedule. Click a workbook name to edit it in the integrated spreadsheet editor. Review and submit preparation; uploading alone does not prepare a dataset. |
| **My Datasets** | Track preparation and reuse inputs. Configurations using pending inputs wait until preparation succeeds. |
| **Storage** | Manage **Uploads**, **Prepared Input Datasets** and **Output Files**. Sections collapse and remember your choices. Check deletion dates and dependency warnings before freeing space. |
| **Parameter sweep** | Generate combinations from a starting configuration. Boolean parameters include true and false automatically; numeric parameters accept lists or ranges. Use **Preview sweep**, then **Add generated configurations**. Review and submit the experiment to queue it. |
| **Import or export YAML settings** | Save or refill an experiment form. YAML contains settings and dataset references, not the input data. Importing starts no jobs. |

The web services **read precomputed UKMOD outputs; they do not run UKMOD**.
A changed policy schedule needs suitable donor files. For your own inputs, follow
the preparation form's format guidance. An experiment's datasets must use a
compatible, common model version.

Service updates do not change submitted experiments: running and queued jobs keep
their original model version, inputs, parameters and random seeds. Existing
prepared datasets are not automatically rebuilt.

**Training/provider datasets are aggregate-only in MultiRun:** you can view and
save permitted charts, but cannot download raw inputs or individual-level outputs.
For your own uploaded inputs, permitted **Download CSV output (.zip)** and comparison
downloads are available; **Include input dataset** optionally bundles retained
inputs. **Download a comparison** appears only for eligible configurations.
The Visualiser's aggregate ZIP is a separate download, also available for training
examples.

## 4. SingleRun: run and explore a simulation

Open SingleRun and select a model card. Public training profiles do not require
MultiRun sign-in. **Quick Start — 20,000 people** is the easiest first choice;
**Quick Start — 50,000 people** uses a larger prepared population. Both have
`startYear` fixed at **2019** and `endYear` defaulting to **2026**, with a maximum
of **2026**. Choose **README** inside a session for that profile's instructions.

1. Check the **Parameters** sidebar, including `endYear`, `fixRandomSeed`, `randomSeedIfFixed` and
   output options. Set `endYear` to **2020** for a shorter test. Choose export
   settings before Build.
2. Click **Build** and wait for **Start**. Build sets up the population, model and
   charts; it can take a while.
3. Click **Start** to run, **Pause** to inspect progress, or **Step** to advance
   incrementally. Pause may need to wait for the current year's calculations to finish.
   Charts update as the simulation progresses.
4. Use the controls below to inspect inputs, charts, logs and available data. The
   **AI** assistant is described in section 5.
5. Download anything you want to keep, then click **Leave** to release the session
   for another user. Session files are removed when the session is cleaned up.

| Control | Purpose |
|:-----------------------|:----------------------------------------------------------------------------|
| **Parameters / Update Params** | Set up a run. During a simulation, Update Params offers only settings permitted to change at runtime. Locked settings require Reset and another Build. |
| **Input Files** | Browse/download accessible inputs; edit permitted workbooks in the spreadsheet editor or upload replacements before Build. Read-only files stay protected. Workbook edits do not automatically repeat input preparation. |
| **Reset** | Reset for another Build. The selected dataset is kept; earlier run folders may remain in Output. |
| **Speed / Grid / Stack** | Adjust execution delay or chart layout. Speed does not allocate more server CPU. |
| **DB** | Inspect permitted input/output tables with read-only queries. Output tables require `exportToDatabase` to have been enabled before Build. |
| **Output** | Download individual files or **Download Complete Run**. Set CSV/database and `persist…` output options before Build. |
| **Console** | Read progress and diagnostics; expand the pane or download the received log. |

Public training sessions permit detailed-data inspection and downloads. Restricted
profiles can disable DB access and detailed exports; use the permissions shown in
the session to see what is available.

The **configurable training session** lets you review/rebuild bundled input tables.
**User-supplied data** has a startup wizard for compatible population/UKMOD files
and a policy schedule, with bundled examples also available. Follow **Review
selected actions → Continue** before Build. The user-data profile caps `popSize`
at **50,000** and has no fixed 2026 ceiling on `endYear`. Longer horizons still
depend on input suitability and resources; long-run capacity is not guaranteed.

### If SingleRun is busy

The **one-session limit covers all four profiles**. **Email me when SingleRun is
available** lets you verify an address and request one notification. It **does not
reserve a place**; another user may take the slot before you return.

After an explicit **Leave**, notices normally wait five minutes in case the previous
user is restarting. Closing the browser without Leave holds the slot until idle
expiry, normally after an hour; there is no additional five-minute delay after
that cleanup. Notification requests expire after seven days.

The optional **SimPaths news and updates** checkbox is separate and unchecked
initially. Without it, your address is used only for the availability service.
Updates signups are retained separately for up to a year and have a management/
withdrawal link. Integration with a SimPaths mailing list remains future work.

## 5. SingleRun: use the AI assistant

Click **AI** to open **JAS-miner**, the JAS-mine AI assistant. Choose a provider,
enter your own provider API key, and select a model and reasoning level. Provider
choices are Gemini, OpenRouter, Anthropic and OpenAI; availability and charges
depend on your provider account. The assistant is optional.

Try questions such as:

- “Explain the parameters and assumptions behind this chart.”
- “Compare the available runs: which settings and outcomes changed?”
- “Help me diagnose this Build error. What is confirmed and what is uncertain?”

The assistant can inspect permitted charts, settings, workbooks, diagnostics,
outputs and database information, and help explain model code. Its tools follow
the session's data-access restrictions. Check scientific documentation and numerical
results when interpreting its answers.

Use **camera** to preview/crop a screenshot, **paperclip** to attach a supported file,
**Edit & Retry** after a failed request, and **Download** to save the conversation
as Markdown.

**Data access matters.** Detailed-data tools are enabled by default for public
training sessions. In the user-data profile they start disabled; the data-access
button lets you enable them. Relevant records can then be sent to your AI provider.
Disabling access starts a fresh conversation but cannot withdraw information already
sent. Text, files and screenshots you attach are shared regardless of that tool setting.

Keys are kept in this browser; **Remember on this device** makes them persist
there. SimPaths Online does not store them server-side. OpenAI requests forward
the key through the SimPaths Online backend to the provider. Use a trusted device
and consider the provider's terms before sharing data.

## 6. Keep results, manage access and report problems

**SingleRun is temporary working storage.** Download needed files before Leave,
session replacement or expiry. Closing the browser does not save a session
permanently: idle sessions are normally cleaned up after an hour without browser
activity. Each current profile has a **10 GiB** storage allowance. If another Build
cannot fit, use **Output → Review storage cleanup**, save needed files and review
deletions before confirming.

**MultiRun normally retains successful output files for seven days after completion.**
Prepared datasets and uploads also have seven-day policies, with clocks linked to
creation/editing and actual processing use. Dependencies protect files needed by
unfinished work or retained datasets. Viewing, selecting or downloading an item
does **not** renew its lifetime. Check dates and protection messages in **Storage**,
**My Datasets** and Results; deletion warnings normally arrive 96 and 24 hours
before removal. Shared training datasets do not expire under this user-file policy.

Aggregate charts and reusable summaries have separate seven-day retention settings.
Storage pressure can reclaim eligible charts unused for 72 hours. These can be
regenerated on request while the required summaries or raw outputs remain available.
Save the aggregate ZIP for longer-term viewing. Explicitly deleting source results
can remove dependent online charts. Server backups support service recovery, not a
promise to recover an individual's expired files.

For time-limited MultiRun access, renewal reminders arrive **one calendar month**
and **seven days** before expiry. Ask the administration to renew it. At ordinary
expiry, access and new starts stop; an already admitted attempt may finish within
its original deadline and at most 48 hours after account expiry. Explicit revocation
stops affected work promptly. Neither event immediately deletes your data: normal
retention applies, and renewed access reaches only files that remain.

**If something fails:** read the status or Console and record the time, service,
experiment/configuration or session reference, settings and steps that led to it.
Send a screenshot or useful log extract to the administration, removing private
data and keys. A maintenance page usually means a short update; retry later.
Avoid duplicate submissions while checking an existing job.

Your testing, feedback and feature suggestions are valued. Tell us what confused
you, what broke and what would help. Capacity, resource allocations, retention and
interface choices can be adjusted. More SingleRun capacity could use resources
currently assigned to MultiRun or be supported by a server upgrade.
