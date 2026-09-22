<!-- (C) Copyright 2026, by Ross Richardson
UK user-supplied data web deployment and isolated preparation.
@author ross richardson
-->
# SimPaths UK — user-supplied data

Anonymous interactive sessions use their own temporary container. Supply a
`population_initial_UK_YEAR.csv` and compatible UKMOD policy output `.txt` files,
then select the start year and policy schedule. UKMOD runs externally. Files must
use the formats and variables expected by desktop SimPaths. Raw database uploads
are not supported in this startup workflow.

Public training files remain in their training subdirectories. Choose **Bundled
public training examples** and **Use bundled example schedule** explicitly to use
them, or upload those same files to test the non-training upload route. Training
mode is disabled: Build never copies the bundled training schedule over your policy
selection. Replacement parameter workbooks can be uploaded at startup; unrelated
editable workbooks also remain available through Input Files before Build/after Reset.

Review identifies the preparation operations before Continue. Uploads are candidates;
Cancel leaves active inputs unchanged. Each confirmed preparation runs in a fresh
Java process in a temporary workspace. Failed preparation leaves Build unavailable
and active inputs intact; correct the inputs and review again. The worker has a
3 GiB heap and a one-hour preparation timeout. File uploads are limited to 512 MiB
each and 2 GiB total candidates, with a 1 GiB free-space reserve. Preparation checks
space for candidate copies plus a 2 GiB working reserve; this is not a guarantee
that arbitrary large donor datasets will fit. Discard candidate uploads to free space.

Use **Configure startup inputs** in Input Files to revisit startup
before the first Build. After the first Build, population, donor and schedule choices are locked for the
session. Use a new session to change them; download results before leaving or expiry.
Ordinary resets/rebuilds use the same simulation JVM and shared output database.

The target population limit is 50,000. Start years currently follow SimPaths UK
support (2011–2024) and must have a supplied population. End year must be at least
the start year; the training profile's 2026 ceiling does not apply. Supply compatible
workbooks for your intended horizon. Desktop clamping/extrapolation and fixed
alignment cutoffs still apply; longer runs are not scientifically validated here.

The user's detailed-data access remains enabled. AI detailed-data tools start
restricted and can be enabled after acknowledgement. Revoking consent restarts the
assistant conversation, not the simulation. Already sent data cannot be withdrawn.
Consent is stored per session in the browser; it controls the integrated assistant,
not what the owner can independently download/share. Uploaded text/screenshots
remain the user's choice. User-data AI conversations are isolated from the global
conversation used by the public demo configurations.

## Build and validation

Build/install the current Java 25 core and package SimPaths first, then:

```bash
python3 deploy/web-user-data/package_image.py --output /tmp/codex-rer/simpaths-user-data-context
docker build -t simpaths-interactive:uk-user-data /tmp/codex-rer/simpaths-user-data-context
```

Initial allocation: 3 GiB simulation heap, 8 GiB container, two CPUs. This is
provisional, allowing a separate 3 GiB preparation heap plus native/server overhead;
combined peak memory and full-run resource use still need measurement. The image
contains no prepared database and does no preparation during Docker build.

Validation initially uses public training examples through the upload/non-training
route. Test with a compatible non-training research dataset when one is available;
training-data success does not establish research-data compatibility.
