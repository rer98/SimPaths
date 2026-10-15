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
training and user-supplied data. For MultiRun, use the separate
[configuration and hosting adapters](multirun/README.md),
[retained model release workflow](multirun/RELEASES.md) and
[native installation runbook](multirun/VM.md).

The [deployment readiness checklist](DEPLOYMENT_CHECKLIST.md) records the passing
local evidence and remaining host-specific checks for both services. No production
VM has been provisioned; use that checklist for installation and validation order.

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

### Testing a security update through the separated SingleRun services

For a local candidate before committing the final update, run the combined
workflow below. It records the exact working sources, including interactive
input files, and refuses source changes during acceptance. The normal Quick Start
release route still requires committed Java sources. The candidate route cannot
automatically promote images.

```bash
TMPDIR=/tmp-codex "$SIMPATHS_TEST_ENV/bin/python" deploy/acceptance/run_security_refresh.py \
  --frontend "$JASMINE_WEB_REPO" --core "$JASMINE_CORE_REPO" \
  --maven-repository /path/to/cached/maven-repository \
  --output "/tmp-codex/security-refresh-$(date +%Y%m%d-%H%M%S)"
```

Use `--dry-run` first to inspect the stages. This workflow requires local Docker,
Chromium, the two existing prepared Quick Start images and a complete offline
Maven cache. Network access is still needed for Dockerfile package updates. It
uses the quiet research sequence's lock in the output's parent directory, so keep
both workflows under `/tmp-codex` to prevent them running together.

The workflow builds/tests core and SimPaths, packages Quick Start 20,000 and
50,000, and runs their real browser checks through independently running public
and private processes. It then builds uniquely tagged training/user-data images
and checks all four training preparation choices and the user-data workflow in a
disposable PostgreSQL deployment. Original interactive aliases and catalogues are
not changed. Image IDs, runtime and input hashes, source inventories and individual
browser reports are retained. Missing private shutdown evidence or failed cleanup
fails the workflow.

If Quick Start passed but a later image build stopped, pass
`--resume-from /path/to/original-security-refresh` and use a **new** `--output`
directory. This verifies the original source receipt, both browser reports and
their cleanup, the current runtime JAR, and the immutable Quick Start image IDs
before continuing with training/user-data images. It does not rerun the accepted
Quick Start models or Maven builds, and it preserves the original failure report.
Changes to model/frontend/input sources require fresh acceptance. Only the two
workflow orchestration drivers (`run_security_refresh.py` and
`run_interactive_rpc_acceptance.py`), their guard test module and this maintainer
guide may differ; those tooling changes are explicitly recorded in the continuation
receipt. Individual browser-test scripts and deployed model/frontend code must match.

If training finished exporting but user-data could not build, also pass
`--reuse-training-from /path/to/retained-interactive-build`. It verifies that
build's source receipt, exported immutable image ID and runtime before building
only user-data. Training browser checks still run. A legacy combined-build log
must show the training export completing before the user-data stage. New runs
build each profile separately and record its image ID and input hashes after
each successful build, so later failures retain a usable checkpoint. Use the
original successful Quick Start evidence for `--resume-from`; the training build
can come from a separate continuation directory.

If both interactive images built and all four training browser modes passed,
but user-data stopped, use `--resume-interactive-from /path/to/that-refresh`
alongside the original `--resume-from`. This verifies both image IDs, runtime
and input hashes, all four individual training reports, and the completed private
shutdown and cleanup receipt. It runs only the user-data browser workflow and
records the prior training report checksums in the combined acceptance record.
The original failed report is preserved. Missing training checks or unconfirmed
cleanup cannot be skipped. The standalone interactive runner's `--profile user-data`
is a partial check; only the combined refresh verifies and carries forward the
other evidence.

An additional 8 GiB free-space check runs before interactive browser preparation,
after the image builds. User-data preparation can consume several GiB before
the first Build, which still requires its normal 2 GiB working-space reserve.
The resume route does not rebuild Java or require a complete Maven dependency
cache. If disposable duplicates are removed from the isolated cache after Java
acceptance, refill it before another full offline Java build; preserve locally
rebuilt artifacts that differ from the home cache.

Use `--prune-build-cache` only when you intend to reclaim unused shared Docker
build cache before and after each interactive image build. It keeps images,
containers and volumes, but future builds can take longer without cached layers.
That option uses Docker's default dangling-cache cleanup. If regular unused cache
still prevents the free-space check passing, `docker builder prune -a -f` also
clears unused build cache associated with older builds. It preserves tagged
images, containers and volumes. Keep the 8 GiB pre-build free-space check; clearing
cache does not replace the remaining space needed for packaging and preparation.

These local processes use the same test account. They test native framing, real
models and browser behaviour; they do not prove the installed distinct-user
permissions, firewall or filesystem quotas. Follow the selected-host readiness
checks before exposing the service publicly. Rebuilt `multirun.jar` is recorded by
the Java build, but this SingleRun workflow does not build/register a new MultiRun
release.

To test restart and outage handling independently, using fictional model data with
real local TLS, Nginx, PostgreSQL and Unix sockets:

```bash
TMPDIR=/tmp-codex "$SIMPATHS_TEST_ENV/bin/python" deploy/acceptance/run_https_rehearsal.py \
  --frontend "$JASMINE_WEB_REPO" --private-rpc \
  --output "/tmp-codex/singlerun-https-rpc-$(date +%Y%m%d-%H%M%S)"
```

This checks ownership, controls, downloads, public/private restarts and retained
capacity. It does not install system services or perform a backup/restore.

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
configuration, with its SimPaths catalogue and a private PostgreSQL DSN. Restart it
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

## Local user-data memory trials

The user-data preparation worker exits before Build. The application JVM stays
alive, so preparation must fit the worker plus that waiting application inside
one container. The existing 8 GiB catalogue allowance is unchanged. This local
comparison tests two operator settings, without promoting either:

| Setting | First case | Second case |
|---|---:|---:|
| Preparation maximum Java heap | 3 GiB | 4 GiB |
| Application/model maximum Java heap | 4 GiB | 4 GiB |
| Container memory / admission reservation | 5 GiB | 5 GiB |
| Docker CPU allowance | 2 | 2 |
| Container swap | Disabled | Disabled |

Each case uploads the public examples, prepares twice before the first Build,
then runs 50,000 people from 2019 through 2026 with seed 606, resets, rebuilds and
runs through 2026 again. It verifies the preparation worker's actual heap command
and exit before Build, all eight annual summary rows, permissions and cleanup.
The second preparation tests a surviving application and warmed caches; compare
the same numbered pass between cases. Stage timings and sampled combined RAM
are retained. Garbage-collection counters are not measured by this procedure.

Keep the laptop otherwise idle and connected to power. Enable networking for
the image build; the simulation cases use local inputs and make no AI-provider
calls. Docker, Chromium and `postgres:17-alpine` must already be available. Allow
at least 8 GiB free on the root and evidence filesystems **after** the image build;
the runner checks again before each case. It takes the existing research sequence
lock in the output parent directory to exclude overlapping benchmark work there.

After Java changes, build the core into the isolated Maven cache and package the
model. The cache must contain current dependencies; omit `-o` if it needs refilling:

```bash
cd ~/git/SimPathsWeb/JAS-mine-core &&
mvn -o -Dmaven.repo.local=/tmp-codex/java-security-rebuild-20261014/maven-repository \
  -Djava.awt.headless=true -DskipTests install &&
cd ../SimPaths &&
TMPDIR=/tmp-codex mvn -o \
  -Dmaven.repo.local=/tmp-codex/java-security-rebuild-20261014/maven-repository \
  -Djava.awt.headless=true \
  -Dtest=SimPathsPreparationHeapTest,SimPathsUserDataStartupTest,SimPathsPreparationStorageTest package
```

Build one user-data candidate under a separate tag. This includes current package
security floors but does not replace or requalify any deployed image:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python deploy/build_local_images.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --profile user-data \
  --work-root /tmp-codex/user-data-memory-image-contexts \
  --docker-storage-path / \
  --tag-suffix memory-20261015
```

Then run both cases without further intervention:

```bash
cd ~/git/SimPathsWeb/SimPaths &&
TMPDIR=/tmp-codex ~/simpaths-browser-tests/venv/bin/python \
  deploy/acceptance/run_user_data_memory_trials.py \
  --frontend "$HOME/git/JAS-mine/JAS-mine-web" \
  --image simpaths-interactive:uk-user-data-memory-20261015 \
  --output "/tmp-codex/user-data-memory-$(date +%Y%m%d-%H%M%S)"
```

The runner verifies the packaged JAR against the local rebuilt JAR and creates
two tiny image variants differing only in `SIMPATHS_PREPARATION_HEAP_MIB`. Both use
the same immutable base image. Bundled workbook versions are identified by that
image; local upload files and test/service sources have separate recorded hashes.
Do not edit those sources or rebuild the JAR while the comparison runs. Reversing
the order is supported with `--preparation-heap-mib 4096 3072` in a new output
directory; one completed pass in each order would help separate cache/order effects.

`comparison.md` summarises preparation times and whole-session RAM peaks.
`report.json` links each case's private logs, effective settings and annual output
checks. Microdata remains inside each disposable session and is removed during
cleanup; small annual summaries and their hashes remain. Sampling gaps, changed
limits, unexpected workers/restarts, failed preparation and unconfirmed cleanup
fail the trial. A successful trial is workload-specific evidence, not proof that
every uploaded dataset or simulation horizon fits 5 GiB. Representative research
inputs and hosted concurrency still require validation before changing defaults.
