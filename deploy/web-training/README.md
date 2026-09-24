<!-- (C) Copyright 2026, by Ross Richardson
Browser user guide for the configurable UK training-data SingleRun deployment.
@author ross richardson
-->

# SimPaths UK — configurable training session

## About this model

SimPaths follows individuals and households over time, modelling how education,
employment, family life, health, care, income and wealth interact. This session
runs the UK model as a single simulation. See the
[SimPaths documentation](https://simpaths.org/) for the scientific methods and
assumptions.

This configuration uses supplied public **training data** for **2019**. It lets
you explore the startup preparation choices and build a population for your run.
Quick Start instead loads a population that has already been constructed.
**Training-data results are for learning and demonstration, not substantive
research analysis.** See the [input-data guide](https://simpaths.org/getting-started/data/).

No files need to be uploaded for a first run. The default requested population is
**50,000**, the default end year is **2026**, and the fixed random seed is **606**.

## Prepare the session

The **Configure SimPaths UK training session** window appears at startup. If you
cancel it, **Build** opens it again when startup confirmation is still needed.
You can use Cancel to read this guide or inspect the simulation page first.

1. Choose whether to recreate either part of the supplied input database:

    | Choice | What it does | For a first run |
    | --- | --- | --- |
    | **Rebuild starting-population database from supplied training files** | Reimports the training population CSV into the starting-population tables and clears the index of saved processed populations. | Leave unchecked to use the supplied prepared tables. |
    | **Rebuild tax/benefit database from supplied training files** | Recreates the donor CSV and tax/benefit tables from the supplied training policy outputs. | Leave unchecked to use the supplied prepared tables. |

    The choices are independent: neither, either one, or both can be selected.
    Leaving them unchecked still allows you to Build and run a simulation.

2. Click **Review selected actions**. Read the actions and the supplied policy
    schedule. Scroll inside the window if the review extends below the visible area.

3. Click **Continue** to confirm the actions and prepare the inputs. Cancel before
    Continue leaves the active files unchanged. Preparation can take several
    minutes; wait for its completion message.

4. On the simulation page, review the parameters and click **Build**. If you opened
    startup by clicking Build, that Build request continues after successful
    preparation. Wait until **Start** becomes available.

Continue also confirms that every Build will use the supplied training policy
schedule, replacing the active `input/EUROMODpolicySchedule.xlsx`. Both schedule
files are read-only in this configuration.

If preparation fails, read the message in the window and Console, correct the
reported problem and review again. Training preparation may have changed some
input tables before failing; retry the affected preparation or start a fresh
session. Startup choices apply before the first Build. Launch a new session to
choose a different dataset or repeat startup configuration after that point.

## Choose parameters and run

For a short first run, set `endYear` to **2020** and leave other settings at their
defaults. After Build, use **Start** to run, **Pause** to inspect results, or
**Step** to advance incrementally. **Grid** and **Stack** change the chart layout.

| Parameter | Meaning in this configuration |
| --- | --- |
| `popSize` | Requested starting population. Default: 50,000; must be positive. Larger populations need more time and resources. The actual number selected can differ from the requested number. |
| `startYear` | Fixed at 2019. |
| `endYear` | Last simulation year; choose 2019–2026. Default: 2026. |
| `fixRandomSeed`, `randomSeedIfFixed` | Use a fixed seed to control random variation. Fixed seeding is on and the seed is 606 by default. |
| `exportToCSV`, `exportToDatabase` | Choose output formats before Build. CSV export is on and database export is off by default. |
| `persistPersons`, `persistBenefitUnits`, `persistHouseholds` | Select detailed individual, benefit-unit and household outputs. A benefit unit is the family grouping used for tax and benefit calculations. |
| Other `persist…` settings | Select which statistical summaries to save. |
| `dataDumpStartTime`, `dataDumpTimePeriod` | Output start offset from the simulation's start year, and the interval between recordings. Defaults: 0 and 1. |

The other sidebar descriptions explain the exposed model options. Consult the
[parameterisation guide](https://simpaths.org/overview/parameterisation/) before
changing scientific assumptions. Keep inputs and settings consistent when
comparing runs; changing a seed alone does not replace the source dataset.

For another run, use **Reset**, adjust available parameters or editable workbooks,
then **Build** again. Ordinary Reset does not repeat startup preparation or unlock
dataset choices. If a recovery action warns that it will replace the session,
download any results you need before confirming.

## Input files and policy assumptions

Use **Input Files** to view the supplied inputs. Editable workbooks can be changed
before Build, including after an ordinary Reset. Files marked read-only have an
explanation beside them. Replacing a file here does not automatically rerun the
starting-population or tax/benefit imports.

This configuration keeps the supplied training policy schedule fixed. It uses
precomputed tax/benefit donor data; it does not run UKMOD to calculate new policy
systems. Choose the **user-supplied data** catalogue entry if you need a different
population, compatible UKMOD outputs or a different policy schedule.

## Results, the assistant and session lifetime

Open **Output** to download generated files, or **DB** to inspect available input
and output database tables. Files and tables may remain empty until the simulation
reaches its first recording point. More years and detailed exports require more
storage. Charts summarise the simulated population.

Detailed training records and exports are available to you. The **AI** assistant
can use detailed-data tools to help explore these public training results and
explain model settings. Refer to the scientific documentation when interpreting
the results.

**Download everything you want to keep before clicking Leave or allowing the
session to expire.** Session files are temporary; closing the browser does not
save them permanently. Earlier runs can remain available after an ordinary
Reset/rebuild.

If Build reports insufficient storage, use **Output → Review storage cleanup**
to review eligible files. Download required results before confirming any
deletions. If space is still insufficient, download your results and start a
fresh session.

## Credits and further reading

SimPaths is developed by **CeMPA (Centre for Microsimulation and Policy Analysis)**
and collaborators, using the **JAS-mine** simulation framework. The JAS-mine web
interface and these web configurations were developed by **Ross Richardson**.
SimPaths is distributed under the **EUPL-1.2** licence; upstream developer and
third-party notices remain applicable.

Follow the [citation guidance for Bronka, van de Ven, Kopasker, Katikireddi and Richiardi (2025)](https://simpaths.org/overview/how-to-cite/)
when reporting work using SimPaths. Record the model version, input data, policy
assumptions and parameter settings used.

- [SimPaths documentation](https://simpaths.org/)
- [Model parameters and assumptions](https://simpaths.org/overview/parameterisation/)
- [Training and research input data](https://simpaths.org/getting-started/data/)
- [SimPaths UK source code and contributors](https://github.com/simpaths/SimPaths)

The linked documentation also covers desktop use. The browser steps and limits
above describe this configurable training session.
