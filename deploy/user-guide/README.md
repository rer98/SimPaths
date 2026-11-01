<!-- (C) Copyright 2026, by Ross Richardson
Maintain and rebuild the user-facing SimPaths Online preview guide.
@author ross richardson
-->

# Maintaining the user guide

Edit [the Markdown source](../SIMPATHS_ONLINE_USER_GUIDE.md), then rebuild the
[PDF](../SimPaths-Online-User-Guide.pdf) with Pandoc and XeLaTeX installed:

```bash
bash deploy/user-guide/build.sh
```

The build uses local fonts and files and needs no network, running application
or database. Check the PDF's pagination, links and tables after editing it.

The guide describes the public preview as of 1 November 2026. Before changing its
limits or policies, check the installed configuration and deployment receipts,
the SingleRun profile READMEs, `multirun/ACCOUNT_ACCESS.md`,
`multirun/LOCAL_WEB.md`, `multirun/EXPERIMENT_ITERATION.md`, and the browser UI.
Framework defaults and older example configurations can differ from the VPS.
Keep account addresses, credentials and private deployment evidence out of the
user-facing guide. Update its date and print footer together when publishing
a revised edition.

Both services have administrator pages. The separate
[administrator guide](../SIMPATHS_ONLINE_ADMIN_GUIDE.md) and
[PDF](../SimPaths-Online-Admin-Guide.pdf) cover their responsibilities and controls;
[account access](../multirun/ACCOUNT_ACCESS.md) documents the host operator commands.
