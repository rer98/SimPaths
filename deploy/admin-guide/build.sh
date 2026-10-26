#!/usr/bin/env bash
# (C) Copyright 2026, by Ross Richardson
# Render the administrator runbook as a linked, printable A4 PDF.
# @author ross richardson
set -euo pipefail
guide_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
pandoc "$guide_dir/SIMPATHS_ONLINE_ADMIN_GUIDE.md" \
  --from=markdown --standalone --pdf-engine=xelatex \
  --include-in-header="$guide_dir/admin-guide/print.tex" \
  --variable papersize=a4 --variable fontsize=10pt \
  --variable mainfont='DejaVu Sans' --variable monofont='DejaVu Sans Mono' \
  --variable geometry:margin=19mm --variable linestretch=1.04 \
  --output="$guide_dir/SimPaths-Online-Admin-Guide.pdf"
