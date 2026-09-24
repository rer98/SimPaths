<!-- (C) Copyright 2026, by Ross Richardson
Browser user guide shared by the 20,000- and 50,000-person Quick Start images.
The packager supplies the configuration-specific heading and population.
@author ross richardson
-->

## About this model

SimPaths follows individuals and households over time, modelling how education,
employment, family life, health, care, income and wealth interact. This session
runs the UK model as a single simulation. See the
[SimPaths model documentation](https://simpaths.org/) for the scientific methods
and assumptions.

Quick Start is for learning the interface and exploring the model using supplied
public **training data**. **Training-data results are not suitable for substantive
research analysis.** See the [input-data guide](https://simpaths.org/getting-started/data/).

The starting population is already prepared, so there are no startup questions
or required uploads. The population size is fixed by the catalogue entry you
launched; choose the other Quick Start entry to use the other population size.
The number of people actually selected can differ from the requested
number.

## Run your first simulation

1. Review **Parameters** in the sidebar. For a short first run, set `endYear` to
   **2020**; the default is **2026**. Leave the other settings at their defaults.
2. Click **Build** and wait until **Start** becomes available. Build loads the
   prepared population and sets up the simulation and charts.
3. Click **Start** to run, or **Step** to advance incrementally. **Pause** lets you
   inspect the current charts and results. **Grid** and **Stack** change the chart
   arrangement without changing the simulation.
4. Open **Output** to download generated files. **DB** lets you inspect available
   input and output database tables. Click **AI** to open the integrated AI
   assistant: ask questions about the model and its parameters, get help using
   the interface, or explore your charts and simulation results.
5. To try another run in the same session, use **Reset**, adjust the available
   parameters, and **Build** again. Reset does not choose a new starting dataset.

## Main parameters and outputs

| Control | Meaning in this configuration |
| --- | --- |
| `popSize` | Fixed at the population requested by this Quick Start entry. |
| `startYear` | Fixed at 2019. |
| `endYear` | Last simulation year; choose 2019–2026. Default: 2026. |
| `fixRandomSeed` | Checked by default, so the simulation uses a specified random seed. |
| `randomSeedIfFixed` | Default: 606. Change it before Build to explore variation in simulated events. |
| `exportToCSV` | Enables CSV exports; on by default. |
| `exportToDatabase` | Enables database exports of the selected outputs; off by default. |
| `persistPersons`, `persistBenefitUnits`, `persistHouseholds` | Select detailed individual, benefit-unit and household outputs. A benefit unit is the family grouping used for tax and benefit calculations. |
| Other `persist…` settings | Select the statistical summaries to save, such as demographic, health and income statistics. |
| `dataDumpStartTime`, `dataDumpTimePeriod` | Control when output recording starts, relative to the start year, and the interval between recordings. Defaults: 0 and 1. |

Export settings must be selected before Build. A file or database can be empty
until the simulation reaches its first recording point. Recording detailed data
for more years uses more session storage.

The sidebar descriptions explain the other exposed model settings. For their
scientific meaning, use the [parameterisation guide](https://simpaths.org/overview/parameterisation/).
Choose settings before Build; use Reset and rebuild when a control is locked.

Quick Start's saved population retains the individual and benefit-unit random
seeds used when it was prepared. Changing `randomSeedIfFixed` affects the
simulation's model-level random generators; it does not draw a new starting
population. Keep the inputs, parameters and seed together when comparing runs.

## Inputs and policy assumptions

**Input Files** shows the supplied workbooks and data. Files marked read-only
cannot be edited or replaced; the explanation appears beside the file. Other
editable workbooks can be changed before Build, including after an ordinary
Reset. Changing a workbook does not automatically recreate the prepared population
or tax/benefit donor data.

This training configuration uses a fixed, supplied policy schedule. Both schedule
files are read-only. Each Build uses the training schedule, replacing the active
`input/EUROMODpolicySchedule.xlsx` with that supplied schedule.

Tax and benefit outcomes come from precomputed donor data. This session does not
run UKMOD to calculate new policy systems. Use the **user-supplied data**
configuration to supply a different starting dataset or compatible UKMOD outputs.
Use the **configurable training** configuration to explore startup preparation
with the bundled training files.

## Results, the assistant and session lifetime

Charts summarise the simulated population. Detailed training records and exports
are available to you, and detailed-data tools are available to the integrated
**AI** assistant. The assistant can help explain parameters and explore available
results; use the model documentation when interpreting findings.

**Download everything you want to keep before clicking Leave or allowing the
session to expire.** These sessions provide temporary working storage. Closing
the browser does not save your work permanently. Ordinary Reset/rebuild can leave
earlier runs available in Output; download important results before any action
that warns it will replace or end the session.

If Build reports insufficient storage, open **Output → Review storage cleanup**.
Download required results, review the proposed deletions, and confirm only those
you no longer need. If the session still cannot accommodate another run, download
your results and launch a fresh session.

## Credits and further reading

SimPaths is developed by **CeMPA (Centre for Microsimulation and Policy Analysis)**
and collaborators, using the **JAS-mine** simulation framework. The JAS-mine web
interface and these web configurations were developed by **Ross Richardson**.
SimPaths is distributed under the **EUPL-1.2** licence; upstream developer and
third-party notices remain applicable.

When reporting work using SimPaths, follow the
[citation guidance for Bronka, van de Ven, Kopasker, Katikireddi and Richiardi (2025)](https://simpaths.org/overview/how-to-cite/).
Record the model version, data, policy assumptions and parameter settings used.

- [SimPaths documentation](https://simpaths.org/)
- [Model parameters and assumptions](https://simpaths.org/overview/parameterisation/)
- [Training and research input data](https://simpaths.org/getting-started/data/)
- [SimPaths UK source code and contributors](https://github.com/simpaths/SimPaths)

The linked documentation also covers desktop use. The browser steps and limits
above describe this Quick Start configuration.
