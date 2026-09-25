<!-- (C) Copyright 2026, by Ross Richardson
Maintainer workflow for building, validating and publishing updated SimPaths web images.
@author ross richardson
-->

# Releasing an updated SimPaths web model

Run these workflows from the SimPaths repository. They require the matching
web-enabled JAS-mine-core and JAS-mine-web revisions; **solveit_SimPathsWeb is not
required**. That repository retains historical research and evidence, with
compatibility commands for existing users.

These instructions cover UK SingleRun: Quick Start 20,000/50,000, configurable
training and user-supplied data. They do not implement MultiRun or provision a
production VM.

The separate [MultiRun configuration tooling](multirun/README.md) validates draft
settings and parameter sweeps without launching simulations. It is not yet a
MultiRun release/deployment workflow.

## 1. Checkouts and tools

Use Linux, Java 25/JDK, Maven, local Docker, Python 3.11+ and Chromium via
Playwright. Docker Compose is additionally needed for the optional Compose test.
Keep the model, core and frontend revisions together in release records.

Set these paths for your own checkouts and virtual environment:

```bash
export JASMINE_WEB_REPO=/path/to/JAS-mine-web
export JASMINE_CORE_REPO=/path/to/JAS-mine-core
export SIMPATHS_TEST_ENV="$HOME/.venvs/simpaths-web"

python3 -m venv "$SIMPATHS_TEST_ENV"
"$SIMPATHS_TEST_ENV/bin/python" -m pip install \
  -r "$JASMINE_WEB_REPO/requirements-vm.txt" \
  -r deploy/acceptance/requirements.txt
"$SIMPATHS_TEST_ENV/bin/python" -m playwright install chromium
```

Install Playwright's Linux system prerequisites if its browser launch reports
missing libraries. The browser environment also runs an isolated frontend during
Quick Start acceptance. Shared image-build and README-update helpers themselves
use only the standard library and Docker CLI; they do not start the frontend.

`--frontend PATH` overrides `JASMINE_WEB_REPO`. The compatibility default is
`~/git/JAS-mine/JAS-mine-web`. The release builder accepts `--core PATH`; otherwise
it uses a sibling `JAS-mine-core` checkout. No checkout needs to live under a
particular user's home directory.

Use `--work-root PATH` on the release builder to choose accessible temporary
storage (compatibility default `/tmp/codex-rer`). Keep the evidence directory on
persistent storage and leave headroom for Docker image layers and simulation output.

## 2. Choose the appropriate update

| Change | Route |
| --- | --- |
| Only model-user README text | [README-only image update](WEB_IMAGES.md#updating-only-the-model-user-documentation); no Java or population rebuild |
| Java or other runtime code | Build/test core and SimPaths; rebuild affected images and run their acceptance checks |
| Inputs or population construction used by Quick Start | Prepare a fresh population, then release from that prepared image |
| Frontend-only behaviour | Update/test JAS-mine-web; rerun relevant browser checks against existing model images |

Commit release source and deployment-tool changes before building Quick Start
releases. The builder checks both Java repositories for changes and untracked
source/deployment files. It permits local changes to `input/DatabaseCountryYear.xlsx`
and `input/EUROMODpolicySchedule.xlsx` because that release route takes inputs from
the prepared image, not those workbooks. **If those input changes are intended for
the release, create a fresh prepared base; they are not incorporated by reuse.**

The existing prepared-profile and query-account checks detect structural/profile
and packaging problems. They cannot establish scientific compatibility with every
model change. Review population-construction changes before reusing saved people.

For ordinary compilation or the interactive-image route, run:

```bash
mvn -f "$JASMINE_CORE_REPO/pom.xml" -Djava.awt.headless=true install
mvn -Djava.awt.headless=true package
```

## 3. Quick Start: build and accept a release

The default bases are the current 20,000/50,000 images in
`JAS-mine-web/deploy/simpaths/models.json`. They must already be present in local
Docker. Use `--catalogue PATH` for another catalogue, or supply `--base-20000 IMAGE`
and/or `--base-50000 IMAGE` for different prepared bases. Explicit bases let a new
maintainer release without any historical laptop images.

Use a new evidence directory, inspect the plan, then run it:

```bash
release_results="$HOME/simpaths-releases/quickstart-$(date +%Y%m%d-%H%M%S)"

"$SIMPATHS_TEST_ENV/bin/python" deploy/web-quickstart/build_release.py \
  --core "$JASMINE_CORE_REPO" --output "$release_results" --dry-run

"$SIMPATHS_TEST_ENV/bin/python" deploy/web-quickstart/build_release.py \
  --core "$JASMINE_CORE_REPO" --output "$release_results"
```

Add `--population 20000` or `--population 50000` to both commands to release one
profile. Omitting it releases both sequentially. Maven can download dependencies;
add `--offline` to use only the local cache. The builder runs Maven tests/install
for core and tests/package for SimPaths even if you compiled them earlier.

For each image it verifies the prepared profile and input contents, refreshes
runtime code and OS packages, verifies the query-account migration receipt, and
runs isolated browser acceptance at the recommended allocation. It records image
IDs, source commits, frontend worktree status and hashes in `status.json`. It writes
`models.quickstart.json` only when all selected profiles pass. The browser suite
checks Build/Step/Start/Pause/Reset, charts, SQL restrictions, outputs, storage
cleanup, private networks and backend authentication.

The temporary contexts are removed. Evidence and candidate images remain after
failure so they can be inspected or acceptance retried; failure does not promote
an image. Read the failed stage's `.log` and browser `report.json`. To retry an
already-packaged candidate without rebuilding, use its exact tag:

```bash
"$SIMPATHS_TEST_ENV/bin/python" deploy/acceptance/run_browser_acceptance.py \
  --image YOUR_CANDIDATE_TAG --population 20000 \
  --detailed-access --deployment-profile --storage-check \
  --output "$HOME/simpaths-releases/acceptance-retry-$(date +%Y%m%d-%H%M%S)"
```

Use the matching population. A successful retry supplements the packaging record;
it does not rewrite the failed release record. Keep both directories beneath the
evidence root supplied during promotion.

### Creating the first or a replacement prepared base

Follow [Quick Start preparation and packaging](web-quickstart/OPERATIONS.md) after
building the JAR. For each required population, prepare a new external package,
package it, and build a locally tagged image. Then pass that tag to
`build_release.py --base-20000 IMAGE` or `--base-50000 IMAGE`. This subjects the
new prepared population to the same release and browser checks as an existing
base. Keep reusable prepared packages and their receipts; remove temporary Docker
contexts when finished. Preparation retains substantial diagnostic data.

## 4. Promote accepted Quick Start images

Use exact candidate tags from the release output, after successful acceptance:

```bash
"$SIMPATHS_TEST_ENV/bin/python" deploy/web-quickstart/promote_release.py \
  --evidence "$HOME/simpaths-releases" --image YOUR_CANDIDATE_TAG
```

Repeat `--image` for both profiles. Inspect the preview, then repeat with `--apply`.
The default evidence root, when omitted, is `~/simpaths-benchmarks` for compatibility.
The release builder's `--promote` option performs this step automatically only
after all selected profiles pass, using the parent of its output directory.

Promotion requires packaging evidence and a passing browser report for the same
local image ID. It updates selected entries rather than replacing the catalogue.
Cleanup preserves all discovered `models*.json` references under the frontend,
explicit `--protect-catalogue`/`--keep-image` references, images used by running or
stopped containers, and one locally available validated rollback per population.
Only evidenced release tags are retired; it never globally prunes Docker. Use
`--keep-image` for any additional prepared bases or rollback versions you need.
Do not edit catalogues or run other image builders concurrently with promotion.

Commit the updated deployment catalogue in JAS-mine-web. Restart the frontend
to load it and launch fresh sessions to check the expected configuration and guide.

## 5. Configurable training and user-supplied data

After compiling core and SimPaths, build the two images:

```bash
python3 deploy/build_local_images.py --prune-build-cache
```

Use `--profile training` or `--profile user-data` for only one. Cache pruning is
optional and shared across projects. See [image operator notes](WEB_IMAGES.md)
for storage checks and explicit context packaging. These builds update the local
`simpaths-interactive:uk-training` and `:uk-user-data` aliases; they do not transfer
images to a server. Record immutable image IDs alongside acceptance evidence.

Start a dedicated local frontend using JAS-mine-web's documented VM-development
configuration, with its SimPaths catalogue and a reachable Redis URL. Restart it
after changing catalogue settings. These two tests use that running frontend
(default `http://127.0.0.1:5001`, configurable with `--url`) and create/delete only
their own sessions. Run them sequentially, leaving existing manual sessions first
if the configured session capacity is one.

```bash
interactive_results="$HOME/simpaths-releases/interactive-$(date +%Y%m%d-%H%M%S)"

for preparation in none population tax both; do
  "$SIMPATHS_TEST_ENV/bin/python" deploy/acceptance/run_training_browser_acceptance.py \
    --prepare "$preparation" --output "$interactive_results/training-$preparation" || break
done

"$SIMPATHS_TEST_ENV/bin/python" deploy/acceptance/run_user_data_browser_acceptance.py \
  --output "$interactive_results/user-data"
```

Check all four training reports and the user-data report for `passed`; stop and
investigate any failure before publishing. User-data fixtures come from this
SimPaths checkout's public training CSV/UKMOD files, exercising the upload route.
Use `--repo PATH` to choose another fixture checkout. Acceptance with a compatible
non-training research dataset remains a separate required validation when one is
available. Existing tests are not evidence of arbitrary dataset compatibility.

For interactive releases, record IDs with `docker image inspect IMAGE --format
'{{.Id}}'` and retain them with the reports. The browser scripts do not themselves
create a full Docker-image vulnerability report.

## 6. Additional deployment checks and transfer

Two-session isolation/concurrency and local Compose acceptance are also available:

```bash
"$SIMPATHS_TEST_ENV/bin/python" deploy/acceptance/run_two_session_acceptance.py \
  --output "$HOME/simpaths-releases/two-session-$(date +%Y%m%d-%H%M%S)"

"$SIMPATHS_TEST_ENV/bin/python" deploy/acceptance/run_vm_acceptance.py \
  --output "$HOME/simpaths-releases/compose-$(date +%Y%m%d-%H%M%S)"
```

Both default to the catalogue's Quick Start 20,000 image; `--image` selects a
candidate. Two-session acceptance uses two 4 GiB / two-CPU containers; its measured
short-test admission limits are 8 GiB available RAM and 6 GiB root-disk free space.
These are not production sizing guarantees. Compose testing is HTTP/development
mode and is not proof of production host firewall, quota or HTTPS isolation.

Transfer validated images using JAS-mine-web's `deploy_prebuilt_vm.py`, which can
be invoked by its absolute path from SimPaths. Follow JAS-mine-web's maintained
`deploy/SESSION_SECURITY.md`, staging acceptance instructions and
`docs/vm-deployment.md` for host validation, transfer and activation. Use recorded
image IDs and run image vulnerability scans before production publication. Avoid
the legacy source-build transfer route for prepared Quick Start images.

## Fast checks when editing these workflows

```bash
"$SIMPATHS_TEST_ENV/bin/python" -m unittest discover -s deploy -p 'test_*.py'
"$SIMPATHS_TEST_ENV/bin/python" -m unittest discover -s deploy/web-quickstart -p 'test_*.py'
"$SIMPATHS_TEST_ENV/bin/python" -m unittest discover -s deploy/acceptance -p 'test_*.py'
```

These are fixture/mock tests, not real Docker builds or simulation acceptance.
Generic image-tool tests live in JAS-mine-web's `tests/test_deployment_*.py`.
