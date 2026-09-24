<!-- (C) Copyright 2026, by Ross Richardson
Browser user guide for preparing and running SimPaths UK with user-supplied data.
@author ross richardson
-->

# SimPaths UK — user-supplied data

## About this model

SimPaths follows individuals and households over time, modelling how education,
employment, family life, health, care, income and wealth interact. This session
runs the UK model as a single simulation. See the
[SimPaths documentation](https://simpaths.org/) for the scientific methods and
assumptions.

Use this configuration to prepare a starting population and tax/benefit donor data
from your own compatible files, select the policy schedule, and run the model.
You can also explicitly select the bundled public training examples to learn the
workflow. **Training-data results are not suitable for substantive research
analysis.** See the [input-data guide](https://simpaths.org/getting-started/data/).

## What you need before starting

| Input | Required format and purpose |
| --- | --- |
| Starting population | A CSV named `population_initial_UK_YEAR.csv`, with `YEAR` matching the selected start year; for example, `population_initial_UK_2019.csv`. It must contain the person, benefit-unit and household variables expected by SimPaths. Renaming an arbitrary CSV is not enough. |
| UKMOD output files | Compatible tab-separated `.txt` donor files containing the required tax/benefit variables. Filenames may contain letters, digits, underscores and hyphens, followed by `.txt`; use no spaces. Each active policy-schedule row must refer to a supplied file. |
| Policy schedule | A table entered in the startup window specifying when each policy applies and the UKMOD system year used for its donor output. |
| Parameter workbooks, if needed | Replacements for existing `.xls` or `.xlsx` inputs, with matching filenames and the sheets and columns the model expects. Keep them compatible with your dataset and simulation horizon. |

The starting population and tax/benefit donors are different inputs: the former
contains the people whose lives will be simulated, while the latter provides
precomputed outcomes for assigning taxes and benefits. A benefit unit is the
family grouping used for tax and benefit calculations.

Prepare research inputs using the
[initial-population guide](https://simpaths.org/getting-started/data/initial-population-uk/)
and [tax-benefit donor guide](https://simpaths.org/getting-started/data/tax-benefit-donors-uk/).
This web session reads UKMOD results; it does not run UKMOD itself. Direct upload
of an `input.mv.db` database is not supported.

## Configure and prepare your inputs

The **Configure SimPaths UK with your data** window opens at startup. You can
Cancel to read this guide or inspect the simulation page; click **Build** to
return when preparation is still required.

1. Set **Input dataset** to **My uploaded files** and choose the **Start year**.
    Supported start years are **2011–2024**; your population file must match.

2. Beside **Starting-population CSV**, click **Choose files…** and select your
    population file. Check that its name appears underneath, in **Uploaded files
    awaiting preparation**.

3. Beside **UKMOD output files**, choose the donor files needed by your schedule.
    Their names appear in that section's own uploaded-files list.

4. If your data or assumptions require different parameter inputs, upload them
    under **Replacement parameter workbooks** before preparing. Otherwise retain
    the supplied workbooks. See the workbook guidance below.

5. Complete **Policy schedule**, using **Add policy** for additional rows:

    | Column | What to enter |
    | --- | --- |
    | **Filename** | The exact uploaded UKMOD filename, including `.txt`. |
    | **Policy start year** | The simulation year from which this policy applies. Each active row needs a distinct start year. A blank start year excludes that row. |
    | **Policy system year** | The policy year used to produce that file in UKMOD. It can differ from the year when you want SimPaths to begin applying it. |
    | **Description** | A label to help you recognise the policy. |

    At least one policy must be active. The earliest policy is used for simulation
    years before the first policy start year; later policies take over at their
    specified start years. For example, a row starting in 2019 applies until a
    later row starting in 2021 takes over. The system years must describe the
    actual UKMOD outputs you supplied.

6. Click **Review selected actions**. Read the summary and check the policy
    schedule. Scroll within the window if necessary. Review does not yet replace
    active inputs.

7. Click **Continue** to prepare the selected population and donor data. After
    successful validation, preparation installs the selected inputs, creates the
    active input database and donor CSV, and applies your policy schedule. Wait
    for the completion message; preparation can take several minutes.

8. On the simulation page, choose your run parameters and click **Build**. If you
    entered startup by clicking Build, that request continues after successful
    preparation. Wait for **Start** to become available, then run the simulation.

Uploads are staged until preparation succeeds. **Cancel** before Continue leaves
the active inputs unchanged; it does not discard staged uploads. Use **Discard
candidate uploads** to clear those uploads and choose files again. Cancelling the
file chooser itself leaves you in the configuration window.

## Try the bundled examples

To practise without supplying files, select **Bundled public training examples
(2019)**, choose start year **2019**, and click **Use bundled example schedule**.
Then review and Continue as above. Selecting the dataset alone does not fill in
the policy table.

The examples remain separate from your uploads. With **My uploaded files**
selected, missing population or donor files are reported rather than silently
replaced with training examples. This configuration does not use SimPaths training
mode: subsequent Builds do not copy the bundled training schedule over your
chosen policy schedule. Any staged replacement workbooks apply in either dataset
mode. Before the first preparation, discard them if you want to retain the
supplied workbook defaults. Discarding uploads does not undo an earlier
successful preparation or edits to active workbooks.

## Parameter workbooks and later input changes

**Replacement parameter workbooks** makes revised inputs available during startup
preparation. These include inputs used to prepare the tax/benefit data, such as
`system_bu_names.xlsx`, and workbooks containing model parameters and targets.
Only existing workbook names can be replaced. `DatabaseCountryYear.xlsx` is
managed by preparation, and `EUROMODpolicySchedule.xlsx` is produced from your
policy table; neither is uploaded as a replacement workbook here.

**Input Files** on the simulation page also permits changes to ordinary editable
workbooks before Build, including after an ordinary Reset. It does not rerun
startup imports. Supply any workbook needed by those imports through startup,
so it is used when the database is prepared. Read-only files have an explanation
beside them; their Edit and Upload buttons are unavailable.

Before the first Build, **Input Files → Configure startup inputs** lets you
revisit your dataset choices. Changes require another review and preparation.
**Once the first Build starts, the population, donor and policy configuration is
locked for that session, including after Reset.** Download any required results
and launch a new session to change those inputs.

## Run parameters and outputs

| Control | Meaning in this configuration |
| --- | --- |
| `popSize` | Requested starting population; default 50,000, allowed range 1–50,000. The actual population selected can differ from the requested number. |
| `startYear` | The year chosen during startup; it must match the prepared population. |
| `endYear` | Last simulation year; default 2026. It must be at least the start year. There is no fixed 2026 ceiling for this configuration. |
| `fixRandomSeed`, `randomSeedIfFixed` | Control random variation. Fixed seeding is on and the seed is 606 by default. |
| `exportToCSV`, `exportToDatabase` | Choose output formats before Build. CSV export is on and database export is off by default. |
| `persistPersons`, `persistBenefitUnits`, `persistHouseholds` | Select detailed individual, benefit-unit and household outputs. |
| Other `persist…` settings | Select the statistical summaries to save. |
| `dataDumpStartTime`, `dataDumpTimePeriod` | Output start offset from the start year, and the interval between recordings. Defaults: 0 and 1. |

For a longer horizon, supply suitable projections, targets and other parameter
inputs as well as a policy schedule. Allowing an end year does not establish that
every input supports that year: some model processes use fixed cutoffs or reuse
values outside their data range. Check the
[parameterisation documentation](https://simpaths.org/overview/parameterisation/)
and the relevant inputs before interpreting a longer projection.

Use **Start**, **Pause** and **Step** to run and inspect the simulation. **Grid**
and **Stack** change the chart arrangement. **Output** provides generated-file
downloads, and **DB** provides access to available input and output tables. Files
and tables may remain empty until the first recording point. Use **Reset** and
Build for another run with the same prepared dataset and revised available
parameters. Keep a record of your inputs, policy schedule and run settings.

## If preparation or Build cannot proceed

- **Missing or invalid input:** read the message in the startup window and
  Console. Check the population filename and year, required columns, donor files
  and policy rows. Correct the inputs and click Review selected actions again.
- **Upload rejected:** check the allowed filename, type and limits displayed by
  the upload controls. Discard unneeded staged files before retrying.
- **Preparation failed:** Build remains unavailable until preparation succeeds.
  A normal validation or preparation failure leaves the active inputs unchanged.
  If a message specifically reports that recovery could not complete, follow
  that message and do not attempt Build.
- **Insufficient storage:** discard unneeded candidate uploads or use
  **Output → Review storage cleanup** to review eligible generated files. Download
  anything needed before confirming deletion. If there is still insufficient
  space, download results and use a fresh session or contact the service operator.

Compatibility checks catch format and preparation problems; they do not establish
scientific suitability. This web workflow has been tested using the supplied
training files; compatibility with a separate research dataset still needs to be
validated for the intended application.

## Your data, the assistant and session lifetime

You can inspect and download your own detailed inputs and outputs. The **AI**
assistant starts with detailed-data access disabled. In that mode it can still
use parameter workbooks, aggregate chart values and permitted status information.

You can enable the assistant's detailed-data tools using its data-access button
and acknowledging the explanation. This can send detailed records to the selected
AI service. Turning access off restarts the assistant conversation, without
restarting the simulation; it cannot withdraw information already sent. Files,
text and screenshots you attach to a conversation are sent by your choice,
regardless of the detailed-data tool setting.

**Download everything you want to keep before clicking Leave or allowing the
session to expire.** The session is temporary working storage. Closing the browser
does not save files permanently. Ordinary Reset/rebuild can leave earlier runs
available; download important results before an action that warns it will replace
or end your session.

## Credits and further reading

SimPaths is developed by **CeMPA (Centre for Microsimulation and Policy Analysis)**
and collaborators, using the **JAS-mine** simulation framework. The JAS-mine web
interface and these web configurations were developed by **Ross Richardson**.
SimPaths is distributed under the **EUPL-1.2** licence; upstream developer and
third-party notices remain applicable.

Follow the [citation guidance for Bronka, van de Ven, Kopasker, Katikireddi and Richiardi (2025)](https://simpaths.org/overview/how-to-cite/)
when reporting work using SimPaths. Record the model version, input and parameter
versions, policy scenario and run settings used.

- [SimPaths documentation](https://simpaths.org/)
- [Model parameters and assumptions](https://simpaths.org/overview/parameterisation/)
- [Input data and compilation guidance](https://simpaths.org/getting-started/data/)
- [SimPaths UK source code and contributors](https://github.com/simpaths/SimPaths)

The linked documentation also covers desktop use. The browser steps and limits
above describe this user-supplied data configuration.
