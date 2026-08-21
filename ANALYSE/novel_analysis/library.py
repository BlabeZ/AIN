from __future__ import annotations

import fcntl
import hashlib
from pathlib import Path

from .completion import validate_completion_manifest
from .schema_validation import schema_errors
from .workspace import (
    ValidationIssue,
    ValidationReport,
    WorkspaceError,
    _read_json,
    _read_jsonl,
    _write_jsonl,
)


def _library_dir(root: Path) -> Path:
    root = Path(root).resolve()
    library_path = root / "library"
    if library_path.is_symlink():
        raise WorkspaceError("the pattern library root cannot be a symlink")
    library_dir = library_path.resolve()
    if not library_dir.is_dir():
        raise WorkspaceError("library directory is missing")
    for path in library_dir.rglob("*"):
        if path.is_symlink():
            raise WorkspaceError(f"symlinks are not allowed inside the pattern library: {path}")
    return library_dir


def validate_library(root: Path) -> ValidationReport:
    root = Path(root).resolve()
    try:
        library_dir = _library_dir(root)
    except WorkspaceError as exc:
        return ValidationReport(False, (ValidationIssue("invalid_library", str(exc)),))

    books_path = library_dir / "books.jsonl"
    if not books_path.exists():
        return ValidationReport(
            False,
            (ValidationIssue("missing_book_registry", "library/books.jsonl is missing"),),
        )
    registered_books: set[str] = set()
    registered_completions: dict[str, dict] = {}
    issues: list[ValidationIssue] = []
    for row_number, registration in enumerate(_read_jsonl(books_path), start=1):
        errors = schema_errors(registration, "library_book.schema.json")
        if errors:
            issues.append(
                ValidationIssue(
                    "invalid_book_registration",
                    f"book row {row_number}: {'; '.join(errors[:3])}",
                    "library/books.jsonl",
                )
            )
        book_id = registration.get("book_id")
        if isinstance(book_id, str):
            if book_id in registered_books:
                issues.append(
                    ValidationIssue(
                        "duplicate_book_registration",
                        f"duplicate registered book: {book_id}",
                        "library/books.jsonl",
                    )
                )
            registered_books.add(book_id)
            book_dir = (root / "books" / book_id).resolve()
            dna_ref = registration.get("book_dna_path")
            if isinstance(dna_ref, str):
                dna_path = (book_dir / dna_ref).resolve()
                if (
                    not book_dir.is_relative_to(root / "books")
                    or not dna_path.is_relative_to(book_dir)
                    or not dna_path.is_file()
                ):
                    issues.append(
                        ValidationIssue(
                            "missing_registered_book_dna",
                            f"registered book {book_id} has no valid Book DNA",
                            "library/books.jsonl",
                        )
                    )
            completion_report = validate_completion_manifest(root, book_id)
            issues.extend(completion_report.issues)
            completion_path = book_dir / "distilled/completion.json"
            if completion_path.is_file():
                completion = _read_json(completion_path)
                registered_completions[book_id] = completion
                actual_completion_hash = hashlib.sha256(completion_path.read_bytes()).hexdigest()
                if (
                    registration.get("source_sha256") != completion.get("source_sha256")
                    or registration.get("completion_sha256") != actual_completion_hash
                ):
                    issues.append(
                        ValidationIssue(
                            "registration_hash_mismatch",
                            f"registered book hashes are stale: {book_id}",
                            "library/books.jsonl",
                        )
                    )

    if len(registered_books) < 2:
        issues.append(
            ValidationIssue(
                "insufficient_registered_books",
                "cross-book validation requires at least two finalized books",
                "library/books.jsonl",
            )
        )
    source_hashes = {
        completion.get("source_sha256")
        for completion in registered_completions.values()
        if isinstance(completion.get("source_sha256"), str)
    }
    if len(source_hashes) < 2:
        issues.append(
            ValidationIssue(
                "insufficient_distinct_sources",
                "registered books must represent at least two distinct source hashes",
                "library/books.jsonl",
            )
        )

    patterns_path = library_dir / "patterns.jsonl"
    if not patterns_path.exists():
        return ValidationReport(
            False,
            (ValidationIssue("missing_patterns", "library/patterns.jsonl is missing"),),
        )

    pattern_ids: set[str] = set()
    for row_number, pattern in enumerate(_read_jsonl(patterns_path), start=1):
        errors = schema_errors(pattern, "cross_book_pattern.schema.json")
        if errors:
            issues.append(
                ValidationIssue(
                    "invalid_pattern_schema",
                    f"pattern row {row_number}: {'; '.join(errors[:3])}",
                    "library/patterns.jsonl",
                )
            )
        pattern_id = pattern.get("pattern_id")
        if isinstance(pattern_id, str):
            if pattern_id in pattern_ids:
                issues.append(
                    ValidationIssue(
                        "duplicate_pattern_id",
                        f"duplicate pattern_id: {pattern_id}",
                        "library/patterns.jsonl",
                    )
                )
            pattern_ids.add(pattern_id)
        source_books = pattern.get("source_books")
        if isinstance(source_books, list):
            book_ids = [
                source.get("book_id")
                for source in source_books
                if isinstance(source, dict) and isinstance(source.get("book_id"), str)
            ]
            if len(set(book_ids)) != len(book_ids):
                issues.append(
                    ValidationIssue(
                        "duplicate_source_book",
                        f"pattern {pattern_id} cites the same source book more than once",
                        "library/patterns.jsonl",
                    )
                )
            pattern_source_hashes = {
                registered_completions[pattern_book_id].get("source_sha256")
                for pattern_book_id in book_ids
                if pattern_book_id in registered_completions
                and isinstance(
                    registered_completions[pattern_book_id].get("source_sha256"), str
                )
            }
            if len(pattern_source_hashes) < 2:
                issues.append(
                    ValidationIssue(
                        "insufficient_pattern_source_diversity",
                        f"pattern {pattern_id} requires two distinct source hashes",
                        "library/patterns.jsonl",
                    )
                )
            for source in source_books:
                if not isinstance(source, dict) or not isinstance(source.get("book_id"), str):
                    continue
                source_book_dir = (root / "books" / source["book_id"]).resolve()
                if source["book_id"] not in registered_books:
                    issues.append(
                        ValidationIssue(
                            "unregistered_source_book",
                            f"pattern {pattern_id} references an unregistered book: {source['book_id']}",
                            "library/patterns.jsonl",
                        )
                    )
                if (
                    not source_book_dir.is_relative_to(root / "books")
                    or not source_book_dir.is_dir()
                    or (root / "books" / source["book_id"]).is_symlink()
                ):
                    issues.append(
                        ValidationIssue(
                            "missing_source_book",
                            f"pattern {pattern_id} references an unknown book: {source['book_id']}",
                            "library/patterns.jsonl",
                        )
                    )
                    continue
                for evidence_ref in source.get("evidence_refs", []):
                    if not isinstance(evidence_ref, str):
                        continue
                    evidence_path = (source_book_dir / evidence_ref).resolve()
                    if (
                        not evidence_path.is_relative_to(source_book_dir)
                        or not evidence_path.is_file()
                        or evidence_ref
                        not in registered_completions.get(source["book_id"], {}).get("artifacts", {})
                    ):
                        issues.append(
                            ValidationIssue(
                                "missing_pattern_evidence",
                                f"pattern {pattern_id} has an invalid evidence reference: {source['book_id']}/{evidence_ref}",
                                "library/patterns.jsonl",
                            )
                        )
    return ValidationReport(not issues, tuple(issues))


def register_distilled_book(root: Path, book_id: str) -> None:
    root = Path(root).resolve()
    library_dir = _library_dir(root)
    books_path = library_dir / "books.jsonl"
    lock_path = library_dir / ".books.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        report = validate_completion_manifest(root, book_id)
        if not report.valid:
            first = report.issues[0]
            raise WorkspaceError(
                f"cannot register book: {first.code}: {first.message}"
            )
        completion_path = root / "books" / book_id / "distilled/completion.json"
        completion = _read_json(completion_path)
        registration = {
            "schema_version": "1.0.0",
            "book_id": book_id,
            "status": "distilled",
            "book_dna_path": "distilled/book_dna.md",
            "source_sha256": completion["source_sha256"],
            "completion_sha256": hashlib.sha256(
                completion_path.read_bytes()
            ).hexdigest(),
        }
        rows = [
            row
            for row in _read_jsonl(books_path)
            if row.get("book_id") != book_id
        ]
        rows.append(registration)
        _write_jsonl(books_path, rows)
