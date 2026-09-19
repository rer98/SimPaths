<!-- (C) Copyright 2026, by Ross Richardson

Repository instructions for attribution of newly created files.

@author ross richardson
-->

# Attribution for new files

When creating a new source, test, script, configuration or documentation file for
this project, include `(C) Copyright 2026, by Ross Richardson`, a useful file/class
description, and `@author ross richardson`, using the format's comment syntax.
In later years use the actual creation year for new files. Preserve existing
copyright/license notices and incorporate existing introductory descriptions.

Java/JavaScript/CSS: block comments. Python/shell/properties/Dockerfile: `#`
comments (Python module docstrings may carry the notice). Markdown/HTML/XML:
valid markup comments. Preserve shebangs, encoding declarations, XML declarations
and Docker parser directives. For JSON, binary assets and generated evidence,
record attribution in COPYRIGHT.md instead of inserting invalid comments or
changing evidence. Keep licenses separate; attribution does not relicense code.

Do not add Ross Richardson attribution to pre-existing upstream JAS-mine-core or
SimPaths files merely because they were modified. Check creation history first.
