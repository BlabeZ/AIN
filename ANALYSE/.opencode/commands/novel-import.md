---
description: Initialize and import a TXT novel into the ANALYSE workspace.
agent: novel-orchestrator
---

Load the `novel-analysis` skill. Import the novel described by `$ARGUMENTS`.

Collect only missing book ID, title, optional author, and TXT path. Run init, ingest, and validate. Report the indexed chapter count, normalized character count, detected encoding, and any suspicious chapter-title samples. Keep all writes inside this project.
