---
description: Start or resume layered analysis for an imported novel.
agent: novel-orchestrator
---

Load the `novel-analysis` skill. Start or resume analysis for `$ARGUMENTS`.

Inspect the book status and existing runs. Create a run only when no suitable resumable run exists. Process extraction jobs in parallel, then link jobs in chapter order through `novel-linker`. Require facts and structure gates before nested arcs, topic reports, and distillation. Stop at failed quality gates and preserve resumable job state.
