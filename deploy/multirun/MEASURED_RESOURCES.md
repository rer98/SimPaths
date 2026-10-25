<!-- (C) Copyright 2026, by Ross Richardson
Operator notes for measured MultiRun starting allocations and larger preview inputs.
@author ross richardson
-->

# Measured MultiRun starting allocations

The expanded MultiRun admission envelope is 100,000 requested initial people,
an ending year through 2070 and up to ten sequential repetitions per
configuration. SingleRun has its own limits. Changing a browser ceiling alone
does not provide enough working storage or expose a larger prepared population.
The VPS needs a new immutable resource release and a configurable training input
registered with that release before the expanded workload can be used there.

`workload_envelope.extended_resource_policy` creates the version-three policy
from a retained version-one release. It keeps the model image, JAR and parameter
defaults unchanged. Its starting heap/container choices use the six successful
two-repetition cases in [MEMORY_TRIALS.md](MEMORY_TRIALS.md), supported by the
earlier full-envelope reference in
[POPULATION_RESOURCES.md](POPULATION_RESOURCES.md). No new scientific campaign
is required to construct the policy.

| Initial population at most | Inclusive years at most | Heap / container, 1–2 repetitions | Heap / container, 3–10 repetitions | Output storage per repetition |
| ---: | ---: | ---: | ---: | ---: |
| 20,000 | 17 | 2.5 / 3.5 GiB | 3 / 4 GiB | 0.5 GiB |
| 20,000 | 52 | 2.5 / 3.5 GiB | 3 / 4 GiB | 1 GiB |
| 50,000 | 17 | 3 / 4 GiB | 3.5 / 4.5 GiB | 1 GiB |
| 50,000 | 52 | 3 / 4 GiB | 3.5 / 4.5 GiB | 2.5 GiB |
| 100,000 | 17 | 3 / 4 GiB | 3.5 / 4.5 GiB | 1.5 GiB |
| 100,000 | 52 | 3.5 / 4.5 GiB | 4 / 5 GiB | 4.5 GiB |

The policy chooses a covering population, then horizon, then repetition band.
Every row has at least 1 GiB of memory beyond the heap. Its storage allowance is
5 GiB for setup (or more if the verified input snapshot requires it), plus the
per-repetition output term. The terms include margin above the observed CSV
output. Ten 100,000-person, 52-year repetitions therefore start with at least 50 GiB of
working storage, rather than the old short-preview allowance of 6.5 GiB.

The smaller memory measurements used two vCPUs and two sequential repetitions
in the same JVM, the default annual CSV collectors and seeds 606/607. The extra
0.5 GiB for three to ten repetitions is a precaution for alpha testing; it is
not a measured ten-repetition guarantee. Different parameters, seeds, population
growth and lower CPU quotas remain reasons to examine real VPS monitoring.
Repetitions increase retained output and runtime; they do not multiply RAM as
if each repetition had its own simultaneous process.

Other supported collector selections, including Gini calculations, use a
bounded fallback of a 4 GiB heap, 5 GiB container and 5 GiB setup plus 5 GiB per
repetition. The fallback also covers up to 60 years for earlier uploaded start
years. It preserves the mandatory output contract. This allowance does not
promise that expensive Gini calculations will meet the runtime limit: the
incomplete overnight Gini run remains useful partial evidence and must not be
represented as a successful full-horizon proof.

CPU remains managed by the existing adaptive scheduler, with the same protected
SingleRun/Visualiser capacity, CPU loans and recalls. Each profile has the
existing two-vCPU target; admission may start it at the scheduler's authorised
floor. The existing confirmed memory/storage growth, bounded recovery attempts
and cumulative runtime limits still apply. Nothing bypasses shared capacity,
filesystem checks or workspace quotas.

## Releases and training inputs

Already accepted jobs keep their original release, inputs, seeds, heap and
resource reservations. Changing the default release does not rewrite them.
Configurations added to an experiment remain pinned to that experiment's
release. The browser preserves this pin when its default input selection
changes.

The existing 20,000/50,000 Quick Start datasets retain their saved populations
and original input receipts. Legacy Quick Starts without an explicit release
use the selected compatible release's resource policy for new experiments;
their accepted jobs keep the recorded older release. Inputs explicitly bound
to an older short-preview storage policy cannot admit newly enabled long work;
prepare those inputs with the larger-run release for a new experiment instead.

`public_training.prepare_example` creates **UK training — configurable
population** from exactly the four public repository training files pinned in
`public_training_sources.json`. It reads them from a stopped, qualified image,
checks their byte hashes, and uses the existing native input-preparation path.
It runs no simulations. The resulting dataset fixes the first year at 2019,
allows population selection, and records the immutable model/resource release.
It does not read changed workbooks from a developer checkout.

Registration preserves provider provenance: all approved users may run these
public training inputs and view aggregates; it does not grant raw input/output
downloads. Ordinary uploads and restricted provider inputs cannot become public
through this importer. The verified source certificate and prepared byte hashes
are both checked before registration.

An update must qualify database/HTTP admission and recovery using fictional
files, publish during an idle MultiRun window behind its maintenance page, and
preserve SingleRun. Snapshot the release catalogue and training-import list
before changing them. On failure before public admission reopens, restore that
metadata and hide any unreferenced new public dataset before restoring old code.
Do not start old code against a version-three policy it cannot understand. After
public admission reopens, accepted new work must be preserved for diagnosis.

## Activation verification — 25 October 2026

The VPS update passed after 223 local checks and 43 native database/HTTP/recovery
checks. The installed review accepts 100,000 initial people, 2019–2070 and ten
repetitions with a 4 GiB heap, 5 GiB container and 50 GiB working allowance for
the configurable public training example. All 12 currently approved users could
resolve it. Original job/seed/budget metadata, SingleRun processes, settings,
firewall and Visualiser assets were preserved. Monitoring resumed with an empty
write buffer, no dropped observations and no writer/exporter error. These checks
ran no additional scientific simulations and rebuilt no model images.

Private deployment evidence is retained in
`/tmp-codex/multirun-envelope-20261025/native-20261025T144037Z-H6aO5o/`.
