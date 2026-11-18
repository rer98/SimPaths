<!-- (C) Copyright 2026, by Ross Richardson
Preparation-only input compaction and immutable dataset revision maintenance.
@author ross richardson
-->

# Prepared input compaction

`SimPathsUserDataPreparation` compacts its closed candidate after the preparation
child has exited and query permissions have been provisioned. It compares complete
logical SQL exports, including values, schema, sequences, users and grants, before
atomically replacing the candidate. Installation and approved-file fingerprints
follow compaction. The existing MultiRun `PrepareDataset` adapter uses this same
preparation route. No compaction runs during simulation Build, Reset or events.

Existing release JARs and dataset receipts remain immutable. New preparation must
use a newly registered release containing the updated preparation classes. The
`deploy.input_compaction` builder can produce that JAR from a retained release;
all scientific and dependency entries outside the three preparation classes are
preserved byte-for-byte. Keep older releases available for their existing datasets
and submitted experiments. A patch is not applied to a running JVM.

For an existing trusted prepared dataset, `deploy.multirun.compact_prepared` creates
a separate directory with a new receipt. It uses the retained model's own H2
library and checks every other input, the model JAR and the retained execution
script. The source remains unchanged even when verification or compaction fails.
Run this operator tool within the normal maintenance, backup and storage fences,
with a private destination on the prepared-data filesystem and enough space for
both revisions and temporary compaction files. Never run it against live workspaces
or browser-supplied database files.

After verifying the candidate, `register_revision` publishes it through the private
`Preparations` maintainer API. Registration is atomic and replayable. It preserves
owner, origins, public-training status, grant expiry and retention age. The original
is retained as an ancestor so revoking its access also revokes the new revision.
Neither publication nor compaction changes any submitted experiment or job's input
binding. The new revision appears with a “(compact)” suffix for future selections.
No browser or RPC operation exposes this maintenance API.

Compaction reduces unused H2 pages, shared prepared storage and input copies made
by future jobs using the compact revision. It does not shrink scientific CSV output
or change the number of copied input snapshots. Existing revisions are not deleted
by this operation; review dependencies and normal retention before retiring them.
