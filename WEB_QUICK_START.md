<!-- (C) Copyright 2026, by Ross Richardson

Prepared Web Quick Start (UK/2019 training profile)

@author ross richardson
-->

# Prepared Web Quick Start (UK/2019 training profile)

Quick Start uses supplied **training data**, not research-ready survey inputs.
Results are for demonstration and learning, not substantive research analysis.
It loads a saved **prepared population** to reduce Build time. The profile fixes
UK/2019 and the packaged requested population (20,000 or 50,000),
unweighted population construction and population-target filtering. Actual person
counts can differ from the requested count because of population selection.

End year 2026 and fixed seed 606 are defaults. Before Build, end year may be
2019–2026, and the seed value and fixed-seed switch are editable. They remain
locked during execution. Saved people and benefit units retain their prepared
seeds; changing the simulation seed affects model-level random generators,
without regenerating the prepared population. Receipt seed/end-year values
record preparation provenance and do not constrain these Build choices.

## Launch

Use Java 25 and the matching web-enabled JAS-mine core. A private writable
workspace must contain the prepared package's `input/` and `profile.json`, the
rebuilt `singlerun.jar`, `webserver.properties` and the deployment README.

```bash
java -cp singlerun.jar simpaths.experiment.SimPathsWebBootstrap
java -cp singlerun.jar simpaths.experiment.SimPathsQuickStart --desktop
```

The second command opens the desktop simulation shell with the same profile and
checks, without repeating startup questions. Normal `java -jar singlerun.jar`
and MultiRun entry points retain their existing defaults and preparation choices.
Use a separate JVM for each session/profile; switching profiles inside one JVM is
not supported.

## Preparation and migration

Normal bootstrap startup no longer generates missing inputs. It requires a
verified prepared package and fails with a preparation/provisioning message when
that package is missing, incompatible, empty or inaccessible. It never silently
falls back to ordinary population construction or overwrites existing inputs.

The coordinating repository provides `prepare_quick_start_profile.py` to create
base inputs and save the processed population in a new external staging directory,
then verify it and perform a fresh-JVM loading check. `package_quick_start_image.py`
creates an isolated Docker build context from that package and the current JAR.
See that repository's `quickstart/README.md` for commands. These tools can move
into SimPaths through a later reviewed change.

The existing administrative command remains available where raw training sources
are installed:

```bash
java -Djava.awt.headless=true -cp singlerun.jar simpaths.experiment.SimPathsWebBootstrap --prepare-only
java -Djava.awt.headless=true -cp singlerun.jar simpaths.experiment.SimPathsWebBootstrap --prepare-only --rebuild-inputs
```

These commands prepare **base inputs only**, not a saved processed population.
Explicit rebuilding replaces the base database and has no automatic backup.
`--rebuild-inputs` without `--prepare-only` is now rejected. Runtime images need
only the prepared database and Excel files; raw preparation CSV/text files remain
in the administrator's original package.

## Build checks and editing

Startup and Build require the versioned profile receipt, the seven populated base
and donor tables, exactly one matching processed record and nonempty population
counts matching the receipt. Before HTTP mutation the request is checked; the
same effective model settings are checked at model Build for desktop and web.
Other scenario parameters and existing Excel editing facilities remain available.

Editing inputs used to construct the prepared population does not regenerate it.
Changed starting-year population-selection targets are not reapplied. Parameters
read during simulation can still affect the starting year and later years.
Changing a policy schedule does not generate tax-benefit donor data. In training
mode, parameter loading replaces the top-level policy schedule with
`input/EUROMODoutput/training/EUROMODpolicySchedule.xlsx`.

Readiness checks establish structure and profile identity, not full scientific
compatibility or dependency freshness after arbitrary edits. Detailed policy-data
compatibility checks remain separate work. There is no blanket input/JAR
fingerprint invalidation: receipt revision and JAR hash record provenance, not a
requirement to regenerate the population after unrelated code changes. Packaging
checks transfer hashes once; ordinary startup/Build does not rescan input files.

Closed databases are inspected in physical read-only mode with `IFEXISTS=TRUE`.
For an already retained processed-population factory, checks use compatible H2
connection settings, issue only SELECTs and roll back their transaction; they do
not close or replace the retained factory. The first build uses its normal input
copy. Rebuild checks also inspect the retained first-build persistence database.

## Session isolation and outputs

The explicit generated Docker context includes only the selected prepared runtime
inputs. It does not relax the SimPaths repository `.dockerignore`. Each container
has its own writable input/database files; do not share a writable H2 volume across
sessions. This manual image path does not implement production orchestration or
the future interactive SingleRun setup UI.

Each build keeps its native input/output directory. The output database remains
shared by all runs within the JVM, with distinct experiment IDs for SQL comparison.
Reset preserves it; final server shutdown closes it. Collector database export
remains an independent setting (default CSV export).

The starting-population and processed-population factories retain the agreed
same-path reuse, replacement on path change, per-operation entity-manager cleanup
and final JVM-shutdown cleanup. No factory lifetime changes are part of this
Quick Start integration. `allowDetailedDataAccess` retains its existing setting;
the future training-only interactive deployment is a separate profile decision.
