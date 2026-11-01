<!-- (C) Copyright 2026, by Ross Richardson
Reproducible PDF rendering of the administrator guide without changing the user guide.
@author ross richardson
-->

# Build the administrator PDF

Edit `../SIMPATHS_ONLINE_ADMIN_GUIDE.md`, then run `bash deploy/admin-guide/build.sh`
from the SimPaths checkout. This requires Pandoc, XeLaTeX, the DejaVu fonts and
the LaTeX packages used in `print.tex`. It writes
`deploy/SimPaths-Online-Admin-Guide.pdf` and does not rebuild the User Guide.

Review the PDF after editing, especially tables, command wrapping and page breaks.
The guide describes the installed service as of 1 November 2026. Verify current
retention and runtime settings against the installed profile and deployment
receipts before changing them, and update the print footer with the edition date.
The administrator interface requires separate qualification and installation;
building the document changes no service or account.
