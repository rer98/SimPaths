<!-- (C) Copyright 2026, by Ross Richardson

Maintainer guide to the draft MultiRun configuration normaliser and its tests.

@author ross richardson
-->

# MultiRun configuration validation

This directory implements the first configuration and local proof tools for web MultiRun.
It accepts plain form data or bounded YAML, expands parameter sweeps, produces a
detached review snapshot and generates configurations for the existing native
MultiRun runner. The local proof tools prepare bundled examples and compare native
execution in fresh working directories. **They do not implement a web service,
upload endpoint, authorisation, queue or production execution sandbox.** Java
production code and existing SingleRun configurations are unchanged.

## Review the example

From the SimPaths repository, use Python 3.11+ with
[requirements.txt](requirements.txt) installed in your development environment:

```bash
python -m deploy.multirun.configuration deploy/multirun/example.yml
python -m deploy.multirun.configuration deploy/multirun/example.yml --native savings-0001
```

The first command prints resolved settings and workload counts. The example
generates three saving-rate configurations, each with ten repetitions: 30
simulations and the same seeds 606–615 in every configuration. The second prints
one native YAML configuration. Neither command writes inputs or starts a model.
The example is a syntax/workload illustration, not a recommended research design.

Generated native YAML assumes separately validated, prepared UK inputs in the
runner's private workspace. It is **not** a substitute for preparing data, checking
dataset permissions or choosing the population persistence policy. `trainingFlag`
is false so explicit prepared schedules are not replaced by training copies.

## API and formats

- `normalise(mapping, limits=Limits(...))`: form/API data to a review snapshot.
- `normalise_yaml(text, limits=...)`: same validation after bounded YAML decoding.
- `import_native_yaml(text, model_release=..., dataset_revision=..., name=...)`:
  import the supported native subset. The repository's current default YAML is
  accepted. Ambiguous omitted seed/year/population/count settings are errors.
- `snapshot.as_dict()`: detached resolved review data, including expected seeds,
  profile version, expanded configurations and totals.
- `snapshot.editable_configuration()` / `editable_yaml()`: export for subsequent
  editing/import; preserves resolved fields and sweep values. `as_dict()` is an
  evidence format, not the editable input format.
- `snapshot.native_configuration(id)` / `native_yaml(id)`: native runner settings.
- `snapshot.configuration_sha256`: content identity of the resolved snapshot,
  including its labels and reference IDs; not proof of dataset permission,
  file integrity or scientific equivalence.

Use the normalisation functions for untrusted inputs. Never construct a
`NormalisedExperiment` directly from client-provided canonical JSON and treat it
as validated. The platform must resolve catalogue/dataset IDs, verify access and
contents, enforce resource policy and pin the actual image/input digests before
submission. This tool validates the syntax of those IDs only.

For a different starting seed, replace the example seed section with:

```yaml
seed_plan:
  mode: starting_seed
  first_seed: "1001"
  repetitions: 10
```

Decimal strings preserve all signed Java 64-bit seed values in a browser. The
existing native +1 progression is always used. Arbitrary seed lists/increments
and nested `randomSeedIfFixed` overrides are rejected. The standard profile is
`simpaths-standard-v1` (606 + repetition index).

Sweep parameters use explicit value lists, or numeric ranges:

```yaml
parameters:
  model_args.savingRate:
    start: 0.04
    end: 0.06
    step: 0.01
```

Ranges include both endpoints and require the end to be reached by whole steps.
Descending ranges require a negative step. Normalised duplicate values are errors.
Multiple dimensions use all combinations. Dimension names are sorted and each
value list retains its order, giving deterministic generated IDs. Explicit and
generated Run Sets share workload limits. Duplicate IDs or effective configurations
are rejected for review in this initial tool. Sweep values override that field in
the base configuration, and all resolved differences are visible in the snapshot.

## Scope and remaining integration

The explicit profile in [schema.py](schema.py) supports basic scalar model settings
and collector settings. The scientific configuration is still a draft:

- IO, saved-grid reuse, lifetime-income features, arbitrary paths, seed overrides,
  union-matching enum changes and diagnostic settings outside this profile are
  rejected. They need separate native/preparation/effect tests before exposure.
- Source types/defaults are checked against Java declarations. Finite values,
  integer bounds, year order and seed overflow are validated; this does not
  establish scientifically meaningful ranges for every parameter.
- This model version's starting-year bounds are 2011–2024. There is no Quick Start
  2026 end-year cap. Dataset-specific year coverage and model extrapolation still
  need validation before running a job.
- The provisional output contract requires annual Person and BenefitUnit CSV
  output from the start. Reese's final schema is pending. Generation does not
  grant permission to download provider-origin microdata.
- The tool currently references one prepared dataset revision per experiment.
  Population/UKMOD/workbook uploads, Run Set input/schedule overrides, immutable
  production preparation receipts, resource admission and actual job provenance
  are later integration work; unknown fields are never silently ignored.
- No browser form or platform queue implementation is included here. The local
  queue proof below uses the generic JAS-mine-web worker; retry controls belong
  to platform state, not scientific YAML.

`Limits` are trusted mechanical parser/expansion ceilings: 64 KiB input, depth 16,
16,000 nodes, 4,096 characters per scalar, 100 Run Sets, 1,000 repetitions and
10,000 total simulations. They can be tightened by the caller and are **not** a
promise that a deployment admits or can execute that workload. Separate trusted
resource limits must be checked before queue admission.

The parser rejects explicit YAML tags, anchors/aliases, merge/duplicate keys,
multiple documents and non-plain objects. Form input gets equivalent structure
and value checks. Parser failures omit uploaded line contents from diagnostics.
Both paths reject arbitrary command, image-path, ownership and platform fields.
Bound request bodies before parsing at any future HTTP endpoint as well.

## Tests

```bash
python -m unittest discover -s deploy/multirun -p 'test_*.py' -v
mvn -Djava.awt.headless=true -Dtest=SimPathsMultiRunConfigurationTest test
```

Python tests cover unsafe inputs, form/YAML/export equivalence, native import,
sweep counts/limits, 64-bit seeds and source-default drift. The committed Java
fixture must match the example generator. The Java test applies it using the
runner's existing assignment and increment methods, checks model/collector fields
and labels, and verifies both ordinary and large starting seeds across independent
Run Sets. It restores launcher static state and performs no population build or
scientific simulation.

Passing these tests demonstrates configuration/assignment compatibility. The
local execution comparison below is a separate check; neither enables web submissions.

## Local preparation and native execution proof

Run from this repository with the Python dependencies above and Java available.
Build `multirun.jar` using the normal SimPaths build first. Choose new, private
directories outside the checkout (the tools refuse existing destinations).
The combined command automatically removes large preparation/run copies on both
success and failure, retaining the evidence:

```bash
python -m deploy.multirun.run_local_proof --output /path/to/new/proof-directory
```

For repeated comparisons against one retained prepared snapshot, use these
separate commands instead:

```bash
python -m deploy.multirun.prepare_training --output /path/to/new/prepared-directory
python -m deploy.multirun.compare_native \
  --prepared /path/to/new/prepared-directory \
  --output /path/to/new/comparison-directory
```

Preparation copies the bundled UK 2019 population, bundled 2011–2026 policy
schedule/donor files and current model parameter workbooks. It **does not read the
checkout's active input database or the two generated selection workbooks**. It
invokes the existing `SimPathsUserDataPreparation` worker and records SHA-256
hashes of the source files, prepared files and the copied model JAR. A successful
receipt is written only after normal worker completion and output checks. Partial
preparation files are removed on failure; the private diagnostic log is retained.

The comparison runs an independently written native reference, a generated
baseline and a generated saving-rate scenario, sequentially. Each uses 2,000
people, 2019–2020 and seeds 606, 607, 608. `-P root` is explicit: the native model
may reuse its processed population within a Run Set. Each Run Set starts with a
separate fresh copy of the same prepared inputs. No hard links or shared writable
databases are used. Hashes are checked again when copying and after execution.

Checks require normal process exit, three distinct expected seeds in native
options files, matching population/years/saving rates, and annual Person and
BenefitUnit records for both years. All exported scientific CSV fields and IDs
are compared exactly as multisets, excluding only the operational `run` column.
The saving-rate scenario must change finite 2020 person non-labour income
(`yMiscPersGrossMonth`) for each seed. This profile disables explicit wealth
projection, so unchanged benefit-unit wealth is not a scenario failure.
Evidence retains configurations, options, CSV files, private logs and a JSON
report; large temporary run directories are removed, including on failure.
If exact baseline comparison fails, the report includes differing-column counts
by year, without publishing row contents. The saving-rate check is recorded
independently. Receipt flags remain scientific fields and are not excluded to
make a non-reproducible run pass.

The tools need several GiB of free disk space and run one JVM at a time (3 GiB
preparation heap, 2 GiB simulation heap, two active Java processors). They check
space before large copies, impose execution deadlines and cap diagnostic logs.
The defaults are **local proof settings**, not production quotas or recommended
research population sizes. No Docker image rebuild is needed for this proof.
H2's existing `AUTO_SERVER` mode needs permission to open a local socket. A
preflight rejects environments that deny that permission before copying inputs.
Use a laptop terminal or permitted test environment; the proof does not alter
native database settings to make a restricted sandbox pass.

All paths and the receipt are trusted maintainer inputs. Read-only file permissions
and hashes detect accidental changes; they do not constitute an upload sandbox,
signed provenance or an authorisation decision. This public-example workflow does
not establish arbitrary uploaded-dataset compatibility, provider-output privacy,
production retry/completion semantics, representative 50,000-person performance,
or agreement with the forthcoming visualiser schema. Those remain separate work.

## Isolated PostgreSQL queue execution proof

The model-owned `queue_adapter.py` connects this configuration and preparation
contract to JAS-mine-web's generic local worker. The scientific Java code is
unchanged. This is a maintainer proof with trusted bundled public data, not a
production upload service or a replacement for the comparison above.

With `multirun.jar` built, Java available, Python requirements installed and a
JAS-mine-web checkout containing the batch worker:

```bash
python -m deploy.multirun.run_queue_proof \
  --frontend /path/to/JAS-mine-web \
  --output /path/to/new/queue-proof-evidence
```

The command uses the frontend's generic disposable PostgreSQL test runner. It
installs dependencies in a temporary environment, runs its database/worker tests,
then prepares public training inputs and submits two fixed saving-rate Run Sets.
Each has 2,000 people, 2019–2020 and seeds 606–608. It runs one JVM at a time on the
laptop; no model Docker image rebuild or running `app.py` is needed. Expect a
similar preparation time to the earlier local proof, followed by six simulations.

Large prepared/workspace copies use the system temporary directory independently
of the evidence directory. Each attempt copies and verifies its model JAR and
prepared input inventory; writable H2 files are never shared between Run Sets.
Completed large workspaces are removed before admitting the next configuration.
The launch space check allows for one full private input copy, the native first
run's snapshot of top-level workbooks/database files, the model JAR and a 1 GiB
reserve. It does not count the UKMOD text files as a second native snapshot.
The reserve is specific to this small proof, not a production storage quota.
On normal success or failure, all temporary model copies and the disposable
database are removed. If process termination is uncertain, the report identifies
the retained workspace rather than deleting files beneath a possibly live process.

The generic queue's digest field identifies the copied JAR in this local proof.
Production execution must bind approved container-image and JAR identities through
release metadata. The adapter resolves the dataset from a trusted prepared-data
path and checks the queued receipt fingerprint and seed plan before launching.
This proof is limited to 2019–2020, at most 2,000 people and three repetitions.
Research-scale execution and uploaded datasets need separate validation.

Completion requires normal process exit, exactly the expected seeds, matching
exported model settings and complete annual Person/BenefitUnit CSVs. The adapter
hashes all CSV files and returns per-seed receipts. Native `options.txt` does not
export `ignoreTargetsAtPopulationLoad`, `lifetimeIncomeGenerate`,
`lifetimeIncomeImpute` or `sIndexTimeWindow`; their runtime values are not independently
confirmed by these receipts. Their launcher assignment is covered by the existing
Java configuration fixture. Collector settings are submitted in generated YAML;
required CSV outputs and annual completeness are checked directly.

Evidence contains options, private logs, fingerprints and final queue states,
rather than retaining large scientific CSV/database copies. A passing queued proof
establishes completion of those configured runs. It does not establish fixed-seed
scientific reproducibility or change the outstanding shared-RNG issue. Use the
separate native comparison for that question.

The generic dispatcher, supervisor and database store live in JAS-mine-web. The
model translation, input verification and scientific-output checks live here.
Neither repository imports anything from private development/planning projects.

### Run the same proof in Docker

Use an installed, approved Temurin 25 runtime image with the usual native libraries:

```bash
python -m deploy.multirun.run_queue_proof \
  --frontend /path/to/JAS-mine-web \
  --container-image simpaths-interactive:uk-user-data \
  --output /path/to/new/container-proof-evidence
```

The proof resolves that tag to an immutable image ID before submission. It uses
the existing image as a runtime: the JAR and prepared public inputs are verified
separately using their preparation receipt. No model-image rebuild is required.
The queue pins the runtime image; the prepared receipt pins the JAR and input
bytes. Only trusted public-data runtime images are suitable for this maintainer
proof; production images must not embed another user's or provider's data.

The frontend runner first runs its PostgreSQL and Docker worker tests. Preparation
still uses the trusted local public-data preparation JVM. Each of the two Run Sets
then runs in its own Docker container, one at a time, with two CPUs, 4 GiB memory,
a 2 GiB Java heap, a process limit and an independent execution deadline. The
container sees read-only prepared inputs and launch files and a private writable
workspace. It receives no queue credentials, Docker socket or external network.
The native Java model, configuration and output validation are unchanged.

The platform executor verifies termination and removes each container before
deleting its large workspace. Evidence includes immutable container IDs and the
launch policy. Uncertain Docker state retains the workspace for reconciliation.
CPU/RAM/process limits are enforced, but bind-workspace storage monitoring is not
a hard filesystem quota. Quota-backed storage and shared SingleRun/batch admission
remain production prerequisites; this command does not enable web submissions.

### Reuse the prepared Quick Start training datasets

The 20,000- and 50,000-person Quick Start images can supply reusable example
datasets. This imports their prepared database and matching workbooks; it does
not regenerate their population or donors. Use only installed, maintainer-approved
public training images. This command is not an uploaded-database import service.
Training results are for learning/testing, not substantive research analysis.

Preview an import, then add `--apply`:

```bash
python -m deploy.multirun.import_quickstart \
  --population 20000 \
  --frontend /path/to/JAS-mine-web \
  --output /path/to/new/prepared-20000
```

For the other profile, use `--population 50000` and a separate new output directory.
The default source is that profile's frontend catalogue image; `--image` selects
an explicitly approved installed image instead. `--jar` defaults to this checkout's
`multirun.jar`. Java 25's `javac` is needed to compile the small verification helper.
No source checkout, model image, existing dataset or catalogue entry is modified.

The importer resolves an immutable image ID, streams only ordinary database and
workbook files from a stopped container, and records their hashes. It explicitly
selects the bundled training policy schedule, as Quick Start does at Build, then
checks population metadata/counts and required donor tables using the selected
JAR in an isolated, read-only container. A receipt is published only after success.
The receipt binds the original profile, source image, model JAR, selected schedule
and prepared file contents. Failed imports remove their large partial copies;
uncertain verification-container state retains them for reconciliation.

Run the queued proof using the imported dataset:

```bash
python -m deploy.multirun.run_queue_proof \
  --frontend /path/to/JAS-mine-web \
  --prepared /path/to/prepared-20000 \
  --output /path/to/new/reuse-proof-evidence
```

This uses the recorded runtime image and JAR, skips preparation, and gives each
Run Set a separate writable input copy. It verifies that all three repetitions
actually loaded the saved population, checks native settings/annual outputs, and
verifies the unchanged source afterwards. The example remains available for later
experiments; the proof removes only its own attempt workspaces and containers.
Its default comparison uses saving rates 0.04 and 0.06, seeds 606–608 and 2019–2020.

The prepared examples retain the 2019 start year and exact requested population
size. Their supplied policy coverage ends in 2026; weights and population-target
settings must match the preparation. The initial adapter permits at most three
repetitions. Other inputs/settings that need a new preparation must produce a new
dataset revision. A different runtime image or JAR also needs revalidation.
The saved population retains its person/benefit-unit seeds; changing the native
MultiRun seed does not resample the starting population. Reuse is not evidence of
scientific equivalence to rebuilding a population or a fix for the reported RNG issue.

For initial validation, the 20,000-person profile uses a 2 GiB heap/4 GiB container;
50,000 uses a 3 GiB heap/5 GiB container. Both have two CPUs and a 10 GiB scratch
allowance. These allocations need measurement for longer research workloads.
Allow disk space for the retained dataset, a private copy, the native input snapshot
and outputs. Import/proof commands neither prune existing images nor delete retained
datasets. Store production datasets in managed persistent storage with quotas;
a temporary laptop directory is only a test location.

### Prepare uploaded or provider inputs

`prepare_inputs.py` reuses the existing `SimPathsUserDataStartup` and
`SimPathsUserDataPreparation` validation inside an isolated container. It accepts
the selected population CSV, UKMOD text files, declared parameter workbooks and
a policy schedule. It does not accept input databases. Country/year and schedule
workbooks are generated from the validated selection. The existing file,
decompression, tabular-record and policy-work limits apply.
The active policy schedule must also include a UKMOD output whose **policy system
year** matches the model's base price year (currently 2015), even if the simulation
starts later. The container helper reads that requirement from the selected model
JAR and rejects a missing base-year policy before starting database preparation.

The internal platform bridge is `dataset_service.prepare_owned()`. Its caller
supplies an authenticated owner and opaque upload IDs; JAS-mine-web resolves the
private files and checks approval, ownership and hashes. A dataset is registered
only after successful Java preparation and post-preparation checks. Failed
preparation removes large temporary copies; uncertain container shutdown retains
its workspace for recovery and never publishes a ready receipt. No HTTP endpoint
or experiment page is enabled by this increment.

`dataset_service.publish_provider()` is a separate administrator-only operation.
It registers a verified prepared revision without granting execution or raw-data
download permission. Origins and grants are platform records, not claims in a
browser request or the model receipt. Reuse the registered dataset revision for
later compatible experiments. Every Run Set/retry still receives a writable copy;
the original remains unchanged. Changed inputs, schedule, model JAR or runtime
require a new verified receipt. These receipts describe imported source tables,
not a fixed saved population like the Quick Start profiles.

The selected-input execution adapter is container-only. The current proof keeps
the development bounds of at most 50,000 people, three repetitions and an end year
no later than 2026. Larger research workloads require measurement and review.
Preparation uses 2 CPUs, a 512 MiB parent heap and the existing 3 GiB child heap,
within a 5 GiB container, with a one-hour deadline. Its 12 GiB workspace monitor
is not a hard filesystem quota.

Run the full public-example proof from SimPaths:

```bash
python -m deploy.multirun.run_input_proof \
  --frontend /path/to/JAS-mine-web \
  --image simpaths-interactive:uk-user-data \
  --output /path/to/new/upload-proof
```

This uses disposable PostgreSQL and private temporary storage. It rejects an
invalid population, prepares valid public examples as uploads, checks own/provider
artifact access, and runs two configurations with seeds 606–608. Large source,
prepared and run copies are removed after confirmed shutdown; evidence remains.
The currently installed model JAR/runtime are reused; no image rebuild is needed.
The proof supplies the 2015, 2019 and 2020 UKMOD examples for its 2019–2020 runs.
Its lightweight schedule regression also checks the original incomplete schedule
and corrected schedule against the native model's policy validation in the JAR.

Before production, integrate these trusted service APIs with authenticated
submission, durable preparation-job admission/recovery, managed prepared-file
locations, retention and hard storage quotas. Reviewed aggregation rules are also
required before publishing provider-derived aggregate downloads. The existing
SingleRun application, Redis coordination and Cloud Run mode are unchanged.
