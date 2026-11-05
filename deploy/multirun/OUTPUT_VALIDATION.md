<!-- (C) Copyright 2026, by Ross Richardson
Supervised completion checks using retained simulation allocations.
@author ross richardson
-->

# Output validation

After every repetition in a configuration has finished and its Java container
has stopped, SimPaths Online checks the output before publishing success. These
checks establish the expected seeds and recorded settings, required CSV files,
complete rows, consistent run identities and coverage of the requested years.
They also fingerprint the exact output bytes. They do not establish the scientific
validity of the estimates or replace the Visualiser's aggregation and suppression
rules.

The supervised path is an explicit operator opt-in:

```toml
[validation]
enabled = true
timeout_seconds = 10800
```

It requires schema 039, the separated private executor and delegated hard helper
limits. Existing configurations, scientific parameters, Java heap settings and
original model deadlines remain unchanged. A trusted, identity-checked clean
container exit must prove that Java finished within that original deadline.
Validation then receives its own finite allowance, at most three hours. Controller
recovery preserves that deadline rather than starting a new allowance.

The attempt keeps its model reservation until validation descendants have stopped
and completion is committed. Validation runs in a separate enforced process group
with that reservation's CPU and RAM allowance. It does not share the controller's
one-vCPU group. A configuration with at least two allocated vCPUs may check two
independent repetitions concurrently. CPU recalls are applied to the validation
group and read back before the corresponding capacity is made available elsewhere.
Memory growth and a second scientific execution are not triggered by validation.

The dispatcher polls workers and can continue supervising other attempts. Cancelling
the configuration or revoking access stops validation and prevents publication.
Controller shutdown stops and joins helpers; recovery rechecks the same retained
outputs under the original attempt identity. It never relaunches Java for that
validation stage.

On success, every repetition's receipt is recorded before the existing atomic
model-to-Visualiser resource handover. The transferred allocation can be resized
to the chart worker's needs. Java, validation and aggregation cannot simultaneously
spend the same reservation.

On validation failure, the configuration requires review and is not automatically
resimulated. Failed validation outputs are protected from failed-simulation scratch
cleanup for seven days after the failed validation finishes. Their presence must
be verified before an operator retries validation. Retention, explicit deletion or
an older cleanup policy may already have removed files; missing data cannot be
recovered merely by reopening an attempt. A live backup omits unfinished output
and restores interrupted validation to review without rerunning the scientific
model. The validation ledger and its original deadline remain in recovery history.

The browser uses **Validating outputs** while checks run. During this stage,
**running** Java simulations and available Visualiser charts remain separate states.
Validation failure is shown as **Output validation needs review**, with ordinary
scientific Retry suppressed. A trusted operator can request validation-only recovery
only after proving termination, retained files, current permissions and available
resources; its prior failure remains in the audit history.

## Private executor ceiling

The old executor ceiling covered chart workers and orchestration only. Validation
now temporarily uses resources that had belonged to Docker model containers outside
that unit. Before enabling this path, its enclosing service must permit at least
the approved shared pool RAM plus 2 GiB for orchestration, and pool CPU plus one
vCPU. These are enclosing ceilings; the existing shared scheduler reservations
still govern allocation. The opt-in preflight rejects a smaller parent ceiling.
Do not change the approved pool, SingleRun holdback or host-service headroom merely
to increase this enclosing ceiling.

Run the focused disposable PostgreSQL proof and native delegated-scope acceptance
before deployment. Installation needs the schema-aware drain workflow; a normal
same-schema application package is insufficient for the initial schema-039 update.
