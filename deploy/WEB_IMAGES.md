<!-- (C) Copyright 2026, by Ross Richardson
Operator notes for packaging all SimPaths web images and their guides.
@author ross richardson
-->

# SimPaths web images: operator notes

Model-specific deployment files live together in this repository:

| Configuration | User guide | Packaging/preparation |
| --- | --- | --- |
| Quick Start 20,000 and 50,000 | [web-quickstart/README.md](web-quickstart/README.md) | [Quick Start operator guide](web-quickstart/OPERATIONS.md) |
| Configurable training | [web-training/README.md](web-training/README.md) | `web-training/package_image.py` and its Dockerfile |
| User-supplied data | [web-user-data/README.md](web-user-data/README.md) | `web-user-data/package_image.py` and its Dockerfile |

The user guides are installed as `/app/README.md` for the browser and assistant.
Keep deployment instructions in this operator document and the Quick Start
operator guide; neither is included in the images. Local Python tools use
`_tool_loader.py` to load the selected checkout without confusing scripts named
`package_image.py` or tools from other SimPaths checkouts.

The two entry points `build_local_images.py` and `update_model_readmes.py` keep
SimPaths profile, tag and guide selection here. Reusable Docker build, cleanup,
README verification and catalogue publication operations live in JAS-mine-web's
`jasmine_web/deployment/`. Select that checkout with `--frontend PATH` or
`JASMINE_WEB_REPO`; by default they use `~/git/JAS-mine/JAS-mine-web`. A missing or
older checkout gives an error explaining how to select/update it. `--help` works
without the frontend checkout or its dependencies. These tools need only Python's
standard library and Docker, not a running frontend or Redis.

Preparation and packaging under `web-quickstart/`, `web-training/` and
`web-user-data/` remain standalone: they do not require JAS-mine-web. Shared
operations are documented in JAS-mine-web's `docs/model-image-tools.md`.

## Full image builds

Build/install the matching JAS-mine-core and package SimPaths with Java 25 and
Maven before packaging a code release. From the SimPaths repository, create a
new build context outside the checkout, for example:

```bash
training_context=$(mktemp -d)/training
python3 deploy/web-training/package_image.py --output "$training_context"
docker build -t simpaths-interactive:uk-training "$training_context"
```

For user-supplied data:

```bash
user_data_context=$(mktemp -d)/user-data
python3 deploy/web-user-data/package_image.py --output "$user_data_context"
docker build -t simpaths-interactive:uk-user-data "$user_data_context"
```

Each destination must be new. Remove these temporary build contexts after use;
they contain large copies of the supplied data. Prefer the SimPaths entry point for
repeated local builds, which handles context cleanup and free-space checks:

```bash
python3 deploy/build_local_images.py --prune-build-cache
```

Use `--profile training` or `--profile user-data` to build only one configuration.
If Docker's storage directory cannot be inspected, add `--docker-storage-path`
with an accessible path on that same filesystem (for example `/` when Docker is
on the root filesystem). The default free-space threshold is 8 GiB per image;
it is a headroom check, not a quota. Cache pruning is opt-in and affects unused
build cache shared by local projects; it does not prune images, volumes or outputs.

Both packagers select the supplied parameter workbooks and public training
examples, never an existing input database or other population/donor files.
Training image creation prepares a fresh base database inside Docker; user-data
image creation leaves the examples inactive and performs no database preparation.
Neither process prepares data in the source checkout.

## Runtime and validation notes

The current catalogue allocates training sessions a 3 GiB simulation heap, 5 GiB
container memory and two CPUs. User-data sessions have a 3 GiB simulation heap,
8 GiB container memory and two CPUs; startup preparation uses a separate process
with a 3 GiB heap and a one-hour timeout. Size the production host for overlapping
work and validate the chosen allocations for the supported workloads.

User-data startup enforces upload and preparation-work budgets and reports
confirmed disk-full/quota errors. Its free-space checks are admission checks,
not a guarantee that arbitrary data can be processed within the available space.

The coordinator holds the browser acceptance scripts and recorded evidence.
Training tests cover neither preparation option, population only, tax only and
both. User-data tests use public training files through the upload route; a
compatible non-training research dataset still needs separate acceptance.

## Updating only the model-user documentation

From SimPaths:

```bash
python3 deploy/update_model_readmes.py --apply
```

Without `--apply`, the tool previews the four current catalogue images. Its
default catalogue is `~/git/JAS-mine/JAS-mine-web/deploy/simpaths/models.json`;
use `--catalogue PATH` to select another catalogue. `--frontend` selects only
the shared tooling checkout; it does not change this catalogue default. `--repo PATH` selects the
SimPaths checkout supplying all four guides; the default is this checkout.

Changing a source README does not update an existing image or running session.
For a documentation-only release, an image derived from the exact validated
image can replace `/app/README.md` in one small final layer. Preserve the runtime
user and read-only file permissions. Do not rerun database preparation or change
the application, inputs or configuration for this operation.

Before promoting such an image, verify that only README content changed, check
the README returned by the running model and rendered by the frontend, and check
that each configuration reaches its expected startup screen. Existing simulation
acceptance evidence remains applicable to unchanged runtime/data layers; this
does not count as a new full simulation acceptance run. Record the derived image
identity and documentation checks, then launch new sessions to see the new guide.

The updater performs the layer/configuration and README-byte checks before
publishing any changes. It updates Quick Start catalogue references and keeps the
training/user-data aliases used by `build_local_images.py`, saving backup tags
for their previous images. Restart the frontend to load the catalogue and check
each configuration in a fresh session. Temporary build contexts, inspection
containers and base tags are removed; previous images and unrelated resources
are retained. Rerunning with unchanged guides does not add another image layer.

The printed evidence directory contains the exact guides, before/after catalogues,
build logs and a report linking source and derived image IDs. To roll back, restore
the original interactive image IDs to their recorded `source_tag` aliases as well
as restoring the saved catalogue. Restoring the catalogue alone does not undo
changes to mutable image aliases. Do not run another image builder or catalogue
editor concurrently with publication.

A normal full rebuild of the training Dockerfile currently places README before
database preparation, so changing it invalidates that preparation layer. Use the
documentation-only approach for this update to avoid that unnecessary work.
