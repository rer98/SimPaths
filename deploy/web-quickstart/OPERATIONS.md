<!-- (C) Copyright 2026, by Ross Richardson
Operator instructions for preparing and packaging SimPaths Quick Start profiles.
@author ross richardson
-->

# Quick Start preparation and packaging

This directory owns the model-specific tooling for both the 20,000- and
50,000-person UK/2019 training profiles. [README.md](README.md) is the user guide
packaged into images. This operator document is not packaged.

The tools were moved from `solveit_SimPathsWeb` into SimPaths on 24 September
2026. Prepared population formats, profile identities, checksums and runtime
behaviour are unchanged. Existing verified packages and images remain usable;
the move itself does not require population preparation or a Java rebuild.

| File | Role |
| --- | --- |
| `prepare_profile.py` | Select training inputs, prepare a saved population, verify it and check loading in a separate JVM. |
| `PrepareQuickStart.java` | Offline helper compiled against the supplied SimPaths JAR. It is not a new runtime entry point. |
| `package_image.py` | Verify a prepared package and create a runtime Docker context. It also supplies the Dockerfile transformation used by coordinated release builds. |
| `Dockerfile` | Runtime image, including query-account setup and the existing non-root security settings. |
| `README.md` | Shared user guide; the packager prefixes the selected population heading. |

## Prepare a new population only when needed

Requirements: Linux, Python 3.11+, JDK 25 (`java` and `javac` on PATH), the matching
web-enabled JAS-mine-core, an up-to-date SimPaths `singlerun.jar`, at least 7 GiB
free for preparation/verification copies and enough memory for a 4 GiB Java heap.
The timeout defaults to one hour per subprocess. Preparation can take several
minutes and retains large intermediate files for diagnosis.

From the SimPaths repository, first check a new destination:

```bash
python3 deploy/web-quickstart/prepare_profile.py \
  --population 20000 \
  --output "$HOME/simpaths-prepared/uk-2019-20000" \
  --dry-run
```

The dry run checks paths and lists inputs without creating files or launching
Java. Remove `--dry-run` to prepare that population. Use `--population 50000`
with a different output directory for the other profile; 50,000 is the default.
`--repo PATH` selects another source checkout/JAR; otherwise the tool uses its
own SimPaths checkout, independently of the current working directory.

Preparation refuses an existing destination or one inside a source tree. It
copies top-level Excel inputs, the supplied 2019 training population CSV and the
training donor files; it never imports an existing input database. Do not edit
inputs while they are being copied. Input and JAR hashes record the actual
artifacts used, including any local changes; keep the JAR consistent with source.

Preparation builds and saves the population without running simulation events.
Verification checks the populated base/donor tables, one matching processed
record, and nonempty household, benefit-unit and person counts. A separate JVM
loads a private copy and must report reuse with the same counts. The existing
shutdown hooks close database factories between stages.

On success:

- `status.json` reports `complete`.
- `package/input/` contains the verified database and effective inputs.
- `package/README.md` contains the model-user guide.
- `package/profile.json` records the profile, counts and verification results.
- `package/checksums.json` records transfer hashes. These are not proof of
  scientific compatibility with arbitrary future model/input changes.
- `logs/`, `tools/`, `work/` and `loading-check/` retain diagnostics and intermediate
  copies; only `package/` is the deployment artifact.

On failure the logs remain and status reports `failed`; no completed package is
published. Correct the problem and use a new destination. Do not commit generated
databases to Git or share a writable H2 database between sessions.

## Package an existing verified population

Build the matching runtime JAR when releasing code changes. From SimPaths:

```bash
python3 deploy/web-quickstart/package_image.py \
  --package "$HOME/simpaths-prepared/uk-2019-20000/package" \
  --output "$HOME/simpaths-contexts/uk-2019-20000"

docker build -t simpaths-quickstart:uk-2019-20000 \
  "$HOME/simpaths-contexts/uk-2019-20000"
```

The context destination must be new and outside the source/package trees. Use
distinct directories and a 50,000-person tag for that profile. The packager checks
transfer hashes and the profile before copying the prepared database, Excel
inputs, receipt, runtime JAR, server configuration, licence and attribution.
Raw CSV/text preparation sources remain in the original package. The context
receives the current user guide while the original package and its hashes stay
unchanged. Remove the temporary context after building when it is no longer needed.

The launcher reads the profile receipt and rejects incompatible population
settings. Changing a requested population in a receipt cannot turn one prepared
profile into the other. Repackaging unrelated code does not itself regenerate the
population. End year and simulation seed are editable defaults, as described in
the user guide.

`SimPathsWebBootstrap --prepare-only [--rebuild-inputs]` prepares base inputs only,
not this saved population. Use `prepare_profile.py` for a complete Quick Start
package. See [WEB_QUICK_START.md](../../WEB_QUICK_START.md) for runtime checks and
desktop/web launch details.

## Release orchestration and documentation-only updates

This repository owns `deploy/web-quickstart/build_release.py` and
`promote_release.py`, with browser runners in `deploy/acceptance/`. The builder
tests the matching core and model, reuses verified prepared bases, and calls the
tools in the checkout specified by `--simpaths`. Source and deployment-tool
changes must be committed before a full release build. Reusable promotion and
retention operations are loaded from the selected JAS-mine-web checkout.

See the [maintainer guide](../MAINTAINER_GUIDE.md) for base selection, first-time
preparation, one/two-profile releases, acceptance, promotion and transfer. No
`solveit_SimPathsWeb` checkout is required; its old commands are compatibility
entry points, and its benchmark evidence remains historical.

For README-only changes, use `python3 deploy/update_model_readmes.py --apply`
instead of rebuilding databases. See [WEB_IMAGES.md](../WEB_IMAGES.md) for
the catalogue option, rollback and verification procedure.

## Local checks

From SimPaths, use the Python environment prepared in the
[maintainer guide](../MAINTAINER_GUIDE.md). Release-workflow tests additionally
exercise browser command help and need the acceptance/frontend Python dependencies:

```bash
"$SIMPATHS_TEST_ENV/bin/python" -m unittest discover -s deploy/web-quickstart -p 'test_*.py'
"$SIMPATHS_TEST_ENV/bin/python" -m unittest discover -s deploy -p 'test_*.py'
```

These tests use small fixtures and mocked Docker operations. They do not prepare
real populations or replace the coordinated browser acceptance tests.
