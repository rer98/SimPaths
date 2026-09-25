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
