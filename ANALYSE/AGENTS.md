# ANALYSE Workspace Rules

- Keep every generated file, temporary artifact, report, and evaluation under this directory.
- Treat `books/<book_id>/source/original.txt` as immutable after ingestion. Re-run ingestion when the source changes.
- Never send the whole novel to one model call. Work through indexed chapter ranges and persisted intermediate files.
- Separate grounded facts from structural interpretation. Chapter fact files may contain only source-supported claims.
- Every fact needs a short exact quote. Run `python3 -m novel_analysis ground-facts <book_id>` before validation.
- Run `python3 -m novel_analysis validate <book_id>` and `python3 -m novel_analysis validate-facts <book_id>` at stage gates.
- Run `python3 -m novel_analysis validate-structure <book_id> --require-complete` before arc analysis and `validate-library` after cross-book distillation.
- Give each parallel agent a unique output file. Never let two agents edit the same JSONL or Markdown report concurrently.
- Keep source-specific names, passages, and event sequences out of cross-book patterns and reusable writing rules.
- Do not hard-code a model provider. Use the active OpenCode model unless the user chooses a routing profile.
- Preserve the book's run manifest and validation reports so work can resume without losing provenance. Versioned reruns use a new book ID in this first release.
- Process link jobs in chapter order with one `novel-linker`; entity and ledger files have a single writer.
