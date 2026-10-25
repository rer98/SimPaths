#!/bin/bash
# (C) Copyright 2026, by Ross Richardson
# Render the maintained Markdown user guide to a standalone, linked A4 PDF.
# @author ross richardson
set -euo pipefail
guide_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
pandoc "$guide_dir/SIMPATHS_ONLINE_USER_GUIDE.md" \
  --from=markdown --standalone --pdf-engine=xelatex \
  --include-in-header="$guide_dir/user-guide/print.tex" \
  --variable papersize=a4 --variable fontsize=11pt \
  --variable mainfont='DejaVu Sans' --variable monofont='DejaVu Sans Mono' \
  --variable geometry:margin=19mm --variable linestretch=1.04 \
  --output="$guide_dir/SimPaths-Online-User-Guide.pdf"
