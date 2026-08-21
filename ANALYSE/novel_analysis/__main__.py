from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .workspace import (
    WorkspaceError,
    chapter_batch,
    create_analysis_run,
    ground_fact_parts,
    ingest_book,
    initialize_book,
    materialize_run_inputs,
    materialize_review_inputs,
    validate_book,
    validate_fact_parts,
    workspace_status,
)
from .structure import validate_structure
from .library import register_distilled_book, validate_library
from .completion import finalize_book


ROOT = Path(__file__).resolve().parent.parent


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m novel_analysis",
        description="Manage the local multi-book novel analysis workspace.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="initialize one book workspace")
    init.add_argument("book_id")
    init.add_argument("--title", required=True)
    init.add_argument("--author")

    ingest = commands.add_parser("ingest", help="normalize and index a TXT source")
    ingest.add_argument("book_id")
    ingest.add_argument("source", type=Path)

    plan = commands.add_parser("plan", help="create extraction jobs for a book")
    plan.add_argument("book_id")
    plan.add_argument("--batch-size", type=int, default=10)
    plan.add_argument("--run-id")

    materialize = commands.add_parser(
        "materialize-inputs", help="materialize all bounded extraction inputs for a run"
    )
    materialize.add_argument("book_id")
    materialize.add_argument("run_id")

    materialize_review = commands.add_parser(
        "materialize-review-inputs",
        help="materialize readable source-and-fact packets for validation and linking",
    )
    materialize_review.add_argument("book_id")
    materialize_review.add_argument("run_id")
    materialize_review.add_argument("--part")

    validate = commands.add_parser("validate", help="validate source and chapter index")
    validate.add_argument("book_id")

    validate_facts = commands.add_parser(
        "validate-facts", help="validate chapter facts and evidence offsets"
    )
    validate_facts.add_argument("book_id")
    validate_facts.add_argument("--require-complete", action="store_true")
    validate_facts.add_argument("--part")

    ground_facts = commands.add_parser(
        "ground-facts", help="resolve evidence quotes to source offsets"
    )
    ground_facts.add_argument("book_id")
    ground_facts.add_argument("--part")

    validate_structure_command = commands.add_parser(
        "validate-structure",
        help="validate annotations, entities, ledgers, and arcs",
    )
    validate_structure_command.add_argument("book_id")
    validate_structure_command.add_argument("--require-complete", action="store_true")

    commands.add_parser(
        "validate-library", help="validate cross-book pattern contracts and source diversity"
    )

    finalize = commands.add_parser("finalize-book", help="seal a fully validated distilled book")
    finalize.add_argument("book_id")

    register = commands.add_parser("register-book", help="register a finalized book for cross-book analysis")
    register.add_argument("book_id")

    status = commands.add_parser("status", help="show analysis progress")
    status.add_argument("book_id")

    batch = commands.add_parser("batch", help="emit source text for a chapter range")
    batch.add_argument("book_id")
    batch.add_argument("--start", type=int, required=True)
    batch.add_argument("--end", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            book_dir = initialize_book(ROOT, args.book_id, args.title, args.author)
            _print_json({"book_id": args.book_id, "path": str(book_dir)})
        elif args.command == "ingest":
            _print_json(ingest_book(ROOT, args.book_id, args.source).__dict__)
        elif args.command == "plan":
            run_dir = create_analysis_run(
                ROOT, args.book_id, args.batch_size, args.run_id
            )
            _print_json({"book_id": args.book_id, "run_path": str(run_dir)})
        elif args.command == "materialize-inputs":
            count = materialize_run_inputs(ROOT, args.book_id, args.run_id)
            _print_json(
                {"book_id": args.book_id, "run_id": args.run_id, "input_count": count}
            )
        elif args.command == "materialize-review-inputs":
            count = materialize_review_inputs(
                ROOT, args.book_id, args.run_id, args.part
            )
            _print_json(
                {
                    "book_id": args.book_id,
                    "run_id": args.run_id,
                    "review_input_count": count,
                }
            )
        elif args.command == "validate":
            report = validate_book(ROOT, args.book_id)
            _print_json(
                {
                    "valid": report.valid,
                    "issues": [issue.__dict__ for issue in report.issues],
                }
            )
            return 0 if report.valid else 1
        elif args.command == "validate-facts":
            report = validate_fact_parts(ROOT, args.book_id, args.require_complete, args.part)
            _print_json(
                {
                    "valid": report.valid,
                    "issues": [issue.__dict__ for issue in report.issues],
                }
            )
            return 0 if report.valid else 1
        elif args.command == "ground-facts":
            grounded = ground_fact_parts(ROOT, args.book_id, args.part)
            _print_json({"book_id": args.book_id, "grounded_evidence_count": grounded})
        elif args.command == "validate-structure":
            report = validate_structure(ROOT, args.book_id, args.require_complete)
            _print_json(
                {
                    "valid": report.valid,
                    "issues": [issue.__dict__ for issue in report.issues],
                }
            )
            return 0 if report.valid else 1
        elif args.command == "validate-library":
            report = validate_library(ROOT)
            _print_json(
                {
                    "valid": report.valid,
                    "issues": [issue.__dict__ for issue in report.issues],
                }
            )
            return 0 if report.valid else 1
        elif args.command == "finalize-book":
            path = finalize_book(ROOT, args.book_id)
            _print_json({"book_id": args.book_id, "completion_path": str(path)})
        elif args.command == "register-book":
            register_distilled_book(ROOT, args.book_id)
            _print_json({"book_id": args.book_id, "registered": True})
        elif args.command == "status":
            _print_json(workspace_status(ROOT, args.book_id))
        elif args.command == "batch":
            payload = chapter_batch(ROOT, args.book_id, args.start, args.end)
            rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            print(rendered, end="")
        return 0
    except (WorkspaceError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
