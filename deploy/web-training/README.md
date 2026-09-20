<!-- (C) Copyright 2026, by Ross Richardson
Configurable UK training-data SingleRun deployment and user guidance.
@author ross richardson
-->

# SimPaths UK interactive training session

This deployment uses public UK training data, starts in 2019 and offers the
applicable desktop startup choices. It uses normal population construction,
not a saved Quick Start population. Defaults are 50,000 people, end year 2026
and fixed seed 606; ordinary Build parameters remain editable within the supplied
training data's supported range. Detailed data and exports are enabled.

Before your first Build, review startup. You can independently rebuild the
starting-population tables or tax/benefit tables from the supplied training files.
Leave both unchecked to use the database prepared when the image was built.
Preparation may take several minutes. A failure can leave partially modified
inputs; review and retry preparation or start a fresh session.

The supplied policy schedule is read-only in startup. SimPaths training mode
copies it over the top-level policy schedule during tax preparation and Build;
editing the top-level schedule would therefore be ineffective. Review identifies
these overwrites and the database tables selected for replacement. Cancel before
Continue makes no changes. Continue authorises the described schedule copy on
subsequent Builds while its contents remain unchanged. Changed schedule contents
require a new review before the first Build, or a new session after Build starts.

UKMOD execution is external. New-file uploads and non-training data setup are
planned separately. The existing Input Files tools are for inspecting/replacing
existing files; replacing data does not automatically regenerate database tables.
Do not change the supplied training schedule. Other scenario parameter workbooks
retain their ordinary model behaviour. Input reconfiguration after the first Build
is outside this startup flow because the model retains database connections and
tax-reference caches. Start a new session for a new input configuration.

Download results before leaving: sessions are temporary. This deployment has not
yet had a full browser or resource acceptance run; 3 GiB heap / 5 GiB container /
2 CPUs are provisional starting allocations based on the 50,000-person tests.
Large parameter choices or preparation can require more resources and disk space.

## Build on the operator's laptop

First build/install the updated JAS-mine-core, then package SimPaths with Maven.
From the SimPaths repository:

```bash
python3 deploy/web-training/package_image.py --output /tmp/codex-rer/simpaths-uk-training-context
docker build -t simpaths-interactive:uk-training /tmp/codex-rer/simpaths-uk-training-context
```

The destination must be new. The packager copies supplied training files and
parameter workbooks, never an existing database. Image creation prepares a fresh
base database inside Docker; it does not alter repository inputs. Use the
SimPaths catalogue's configurable training entry after building this image.
