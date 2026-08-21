from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .schema_validation import schema_errors


SCHEMA_VERSION = "1.0.0"
MATERIALIZED_JSON_LINE_LIMIT = 1_600
BOOK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
FACT_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])C[0-9]{6}-(?:E|I|S|L)[0-9]{3}(?![A-Za-z0-9])"
)
ARC_REFERENCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])ARC-[A-Z]-[0-9]{4}(?![A-Za-z0-9])"
)
CHAPTER_HEADING_PATTERN = re.compile(
    r"^[ \t\u3000]*(?P<heading>"
    r"第[0-9０-９零〇○一二三四五六七八九十百千万两]+[章节回]"
    r"|序章|楔子|引子|后记|尾声)"
    r"(?:[ \t\u3000]*[：:、.·\-]?[ \t\u3000]*(?P<title>[^\n]*?))?[ \t\u3000]*$",
    re.MULTILINE,
)

BOOK_DIRECTORIES = (
    "source",
    "index",
    "facts/chapter_facts",
    "facts/chapter_annotations",
    "ledgers",
    "arcs/reports",
    "analysis",
    "distilled",
    "runs",
    "eval",
)


class WorkspaceError(ValueError):
    pass


@dataclass(frozen=True)
class Chapter:
    chapter_id: int
    title: str
    heading: str | None
    kind: str
    char_start: int
    char_end: int
    content_start: int
    content_end: int
    raw_text: str
    content: str


@dataclass(frozen=True)
class IngestResult:
    book_id: str
    chapter_count: int
    front_matter_count: int
    char_count: int
    source_sha256: str
    detected_encoding: str


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    issues: tuple[ValidationIssue, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_book_id(book_id: str) -> None:
    if not BOOK_ID_PATTERN.fullmatch(book_id):
        raise WorkspaceError(
            "book_id must use 1-64 lowercase ASCII letters, digits, or hyphens"
        )


def _validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise WorkspaceError("run_id contains unsupported characters")


def _book_dir(root: Path, book_id: str) -> Path:
    _validate_book_id(book_id)
    root = Path(root).resolve()
    books_dir = root / "books"
    candidate = books_dir / book_id
    if books_dir.is_symlink() or candidate.is_symlink():
        raise WorkspaceError("symlinks are not allowed in book workspace roots")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise WorkspaceError("book path escapes the analysis workspace")
    return resolved


def _reject_symlinks(book_dir: Path) -> None:
    if not book_dir.exists():
        return
    for path in book_dir.rglob("*"):
        if path.is_symlink():
            raise WorkspaceError(f"symlinks are not allowed inside a book workspace: {path}")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    content = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _materialized_text_chunks(text: str) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    escaped_length = 0
    for character in text:
        if ord(character) < 0x20:
            character_length = 2 if character in "\b\f\n\r\t" else 6
        else:
            character_length = 2 if character in {'"', "\\"} else 1
        if current and escaped_length + character_length > MATERIALIZED_JSON_LINE_LIMIT:
            chunks.append("".join(current))
            current = []
            escaped_length = 0
        current.append(character)
        escaped_length += character_length
    if current or not chunks:
        chunks.append("".join(current))
    return chunks


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise WorkspaceError(f"JSON file {path} must contain an object")
        return value
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"cannot read JSON file {path}: {exc}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise WorkspaceError(
                        f"JSONL row {line_number} in {path} must be an object"
                    )
                rows.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"cannot read JSONL file {path}: {exc}") from exc
    return rows


def decode_source(data: bytes) -> tuple[str, str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError as exc:
            raise WorkspaceError("invalid UTF-16 source") from exc

    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise WorkspaceError("source encoding is unsupported; use UTF-8, UTF-16, or GB18030")


def _content_start_after_heading(text: str, heading_end: int) -> int:
    if heading_end < len(text) and text[heading_end] == "\n":
        return heading_end + 1
    return heading_end


def split_chapters(text: str) -> list[Chapter]:
    matches = list(CHAPTER_HEADING_PATTERN.finditer(text))
    if not matches:
        return [
            Chapter(
                chapter_id=1,
                title="正文",
                heading=None,
                kind="chapter",
                char_start=0,
                char_end=len(text),
                content_start=0,
                content_end=len(text),
                raw_text=text,
                content=text,
            )
        ]

    chapters: list[Chapter] = []
    if text[: matches[0].start()].strip():
        front_end = matches[0].start()
        chapters.append(
            Chapter(
                chapter_id=0,
                title="卷首内容",
                heading=None,
                kind="front_matter",
                char_start=0,
                char_end=front_end,
                content_start=0,
                content_end=front_end,
                raw_text=text[:front_end],
                content=text[:front_end],
            )
        )

    for sequence, match in enumerate(matches, start=1):
        next_start = matches[sequence].start() if sequence < len(matches) else len(text)
        content_start = _content_start_after_heading(text, match.end())
        title = (match.group("title") or "").strip() or match.group("heading")
        chapters.append(
            Chapter(
                chapter_id=sequence,
                title=title,
                heading=match.group(0).strip(),
                kind="chapter",
                char_start=match.start(),
                char_end=next_start,
                content_start=content_start,
                content_end=next_start,
                raw_text=text[match.start() : next_start],
                content=text[content_start:next_start],
            )
        )
    return chapters


def initialize_book(
    root: Path, book_id: str, title: str, author: str | None = None
) -> Path:
    if not title.strip():
        raise WorkspaceError("title cannot be empty")
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    if (book_dir / "book.json").exists():
        raise WorkspaceError(f"book already exists: {book_id}")

    for relative_path in BOOK_DIRECTORIES:
        (book_dir / relative_path).mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "book_id": book_id,
        "title": title.strip(),
        "author": author.strip() if author else None,
        "status": "initialized",
        "created_at": _utc_now(),
        "source": None,
    }
    _write_json(book_dir / "book.json", manifest)
    for relative_path in (
        "index/entities.jsonl",
        "ledgers/state_events.jsonl",
        "ledgers/state_checkpoints.jsonl",
        "ledgers/thread_events.jsonl",
        "ledgers/clue_events.jsonl",
        "arcs/arcs.jsonl",
    ):
        (book_dir / relative_path).touch()
    return book_dir


def ingest_book(root: Path, book_id: str, source_path: Path) -> IngestResult:
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    manifest_path = book_dir / "book.json"
    if not manifest_path.exists():
        raise WorkspaceError(f"unknown book: {book_id}")
    manifest = _read_json(manifest_path)
    if manifest.get("source") is not None:
        raise WorkspaceError(
            "book source is immutable after ingestion; use a new book_id for a revised source"
        )

    source_path = Path(source_path)
    if not source_path.is_file():
        raise WorkspaceError(f"source file does not exist: {source_path}")
    decoded, detected_encoding = decode_source(source_path.read_bytes())
    normalized = decoded.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        raise WorkspaceError("source is empty")
    source_sha256 = _sha256_text(normalized)
    chapters = split_chapters(normalized)

    original_path = book_dir / "source/original.txt"
    _write_bytes(original_path, normalized.encode("utf-8"))
    index_rows = []
    for chapter in chapters:
        index_rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "book_id": book_id,
                "chapter_id": chapter.chapter_id,
                "title": chapter.title,
                "heading": chapter.heading,
                "kind": chapter.kind,
                "char_start": chapter.char_start,
                "char_end": chapter.char_end,
                "content_start": chapter.content_start,
                "content_end": chapter.content_end,
                "char_count": len(chapter.content),
                "content_sha256": _sha256_text(chapter.content),
                "source_sha256": source_sha256,
            }
        )
    _write_jsonl(book_dir / "index/chapters.jsonl", index_rows)

    manifest.update(
        {
            "status": "ingested",
            "updated_at": _utc_now(),
            "source": {
                "original_name": source_path.name,
                "stored_path": "source/original.txt",
                "detected_encoding": detected_encoding,
                "normalized_encoding": "utf-8",
                "sha256": source_sha256,
                "char_count": len(normalized),
                "chapter_count": sum(
                    chapter.kind == "chapter" for chapter in chapters
                ),
                "front_matter_count": sum(
                    chapter.kind == "front_matter" for chapter in chapters
                ),
                "ingested_at": _utc_now(),
            },
        }
    )
    _write_json(manifest_path, manifest)

    return IngestResult(
        book_id=book_id,
        chapter_count=manifest["source"]["chapter_count"],
        front_matter_count=manifest["source"]["front_matter_count"],
        char_count=len(normalized),
        source_sha256=source_sha256,
        detected_encoding=detected_encoding,
    )


def create_analysis_run(
    root: Path,
    book_id: str,
    batch_size: int = 10,
    run_id: str | None = None,
) -> Path:
    if batch_size < 1 or batch_size > 50:
        raise WorkspaceError("batch_size must be between 1 and 50")
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    rows = _read_jsonl(book_dir / "index/chapters.jsonl")
    chapter_ids = [row["chapter_id"] for row in rows if row.get("kind") == "chapter"]
    if not chapter_ids:
        raise WorkspaceError("book has no indexed chapters")

    runs_dir = book_dir / "runs"
    existing_runs = [
        path for path in runs_dir.iterdir()
        if path.is_dir() and path.name != "_recovery"
    ]
    required_run_files = (
        "manifest.json",
        "jobs/extract.jsonl",
        "jobs/link.jsonl",
        "jobs/analyze.jsonl",
        "jobs/distill.jsonl",
    )
    complete_runs = []
    for path in existing_runs:
        if not all((path / relative_path).is_file() for relative_path in required_run_files):
            continue
        try:
            _read_json(path / "manifest.json")
            for job_file in required_run_files[1:]:
                _read_jsonl(path / job_file)
        except WorkspaceError:
            continue
        complete_runs.append(path)
    if complete_runs:
        raise WorkspaceError(
            "this book already has an analysis run; resume it instead of creating overlapping outputs"
        )
    if existing_runs:
        recovery_dir = runs_dir / "_recovery"
        recovery_dir.mkdir(exist_ok=True)
        for incomplete_run in existing_runs:
            target = recovery_dir / incomplete_run.name
            if target.exists():
                target = recovery_dir / f"{incomplete_run.name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            incomplete_run.replace(target)

    run_id = run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ")
    _validate_run_id(run_id)
    run_dir = book_dir / "runs" / run_id
    if run_dir.exists():
        raise WorkspaceError(f"run already exists: {run_id}")
    (run_dir / "jobs").mkdir(parents=True)
    (run_dir / "reports").mkdir()
    (run_dir / "inputs").mkdir()

    jobs = []
    for offset in range(0, len(chapter_ids), batch_size):
        batch = chapter_ids[offset : offset + batch_size]
        start_chapter, end_chapter = batch[0], batch[-1]
        job_id = f"extract-{start_chapter:06d}-{end_chapter:06d}"
        jobs.append(
            {
                "schema_version": SCHEMA_VERSION,
                "job_id": job_id,
                "stage": "extract",
                "status": "pending",
                "book_id": book_id,
                "chapter_ids": batch,
                "start_chapter": start_chapter,
                "end_chapter": end_chapter,
                "output_path": (
                    "facts/chapter_facts/"
                    f"part-{start_chapter:06d}-{end_chapter:06d}.jsonl"
                ),
                "input_path": f"runs/{run_id}/inputs/{job_id}.json",
            }
        )
    _write_jsonl(run_dir / "jobs/extract.jsonl", jobs)
    link_jobs = [
        {
            **job,
            "job_id": job["job_id"].replace("extract-", "link-"),
            "stage": "link",
            "input_path": job["output_path"],
            "output_path": job["output_path"].replace(
                "facts/chapter_facts/", "facts/chapter_annotations/"
            ),
        }
        for job in jobs
    ]
    _write_jsonl(run_dir / "jobs/link.jsonl", link_jobs)
    analyze_job = {
        "schema_version": SCHEMA_VERSION,
        "job_id": "analyze-book",
        "stage": "analyze",
        "status": "blocked",
        "book_id": book_id,
        "depends_on": [job["job_id"] for job in link_jobs],
        "output_paths": [
            "arcs/arcs.jsonl",
            "analysis/characters.md",
            "analysis/pacing.md",
            "analysis/hooks.md",
            "analysis/payoffs.md",
        ],
    }
    _write_jsonl(run_dir / "jobs/analyze.jsonl", [analyze_job])
    distill_job = {
        "schema_version": SCHEMA_VERSION,
        "job_id": "distill-book",
        "stage": "distill",
        "status": "blocked",
        "book_id": book_id,
        "depends_on": [analyze_job["job_id"]],
        "output_path": "distilled/book_dna.md",
    }
    _write_jsonl(run_dir / "jobs/distill.jsonl", [distill_job])

    book_manifest = _read_json(book_dir / "book.json")
    run_manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "book_id": book_id,
        "source_sha256": book_manifest["source"]["sha256"],
        "status": "planned",
        "batch_size": batch_size,
        "job_count": len(jobs),
        "job_counts": {
            "extract": len(jobs),
            "link": len(link_jobs),
            "analyze": 1,
            "distill": 1,
        },
        "created_at": _utc_now(),
        "stages": ["extract", "validate", "link", "analyze", "distill"],
    }
    _write_json(run_dir / "manifest.json", run_manifest)
    return run_dir


def materialize_run_inputs(root: Path, book_id: str, run_id: str) -> int:
    _validate_run_id(run_id)
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    source_report = validate_book(root, book_id)
    if not source_report.valid:
        raise WorkspaceError(
            f"source validation failed before materialization: {source_report.issues[0].code}"
        )
    run_dir = book_dir / "runs" / run_id
    manifest_path = run_dir / "manifest.json"
    jobs_path = run_dir / "jobs/extract.jsonl"
    if not manifest_path.is_file() or not jobs_path.is_file():
        raise WorkspaceError(f"analysis run is incomplete or unknown: {run_id}")
    manifest = _read_json(manifest_path)
    book_manifest = _read_json(book_dir / "book.json")
    if (
        manifest.get("book_id") != book_id
        or manifest.get("source_sha256") != (book_manifest.get("source") or {}).get("sha256")
    ):
        raise WorkspaceError("run manifest does not match the current immutable source")

    source = (book_dir / "source/original.txt").read_text(encoding="utf-8")
    if _sha256_text(source) != manifest.get("source_sha256"):
        raise WorkspaceError("source changed while materializing run inputs")
    chapter_index = {
        row["chapter_id"]: row
        for row in _read_jsonl(book_dir / "index/chapters.jsonl")
    }
    jobs = _read_jsonl(jobs_path)
    rendered_inputs: list[tuple[Path, dict[str, Any]]] = []
    for job in jobs:
        chapters = []
        for chapter_id in job.get("chapter_ids", []):
            if chapter_id not in chapter_index:
                raise WorkspaceError(f"job references unknown chapter: {chapter_id}")
            item = dict(chapter_index[chapter_id])
            chapter_text = source[item["char_start"] : item["char_end"]]
            item["raw_text_chunks"] = _materialized_text_chunks(chapter_text)
            chapters.append(item)
        if not chapters or len(chapters) > 50:
            raise WorkspaceError(f"job has an invalid chapter count: {job.get('job_id')}")
        if sum(
            len(chunk)
            for item in chapters
            for chunk in item["raw_text_chunks"]
        ) > 500_000:
            raise WorkspaceError(f"job exceeds the 500000-character limit: {job.get('job_id')}")
        input_path = (book_dir / job["input_path"]).resolve()
        if not input_path.is_relative_to(run_dir.resolve() / "inputs"):
            raise WorkspaceError(f"job input path escapes its run: {job.get('job_id')}")
        rendered_inputs.append(
            (
                input_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "job_id": job["job_id"],
                    "book_id": book_id,
                    "source_sha256": manifest["source_sha256"],
                    "output_path": job["output_path"],
                    "chapters": chapters,
                },
            )
        )
    for input_path, payload in rendered_inputs:
        _write_json(input_path, payload)
    return len(rendered_inputs)


def validate_book(root: Path, book_id: str) -> ValidationReport:
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    issues: list[ValidationIssue] = []
    manifest_path = book_dir / "book.json"
    source_path = book_dir / "source/original.txt"
    index_path = book_dir / "index/chapters.jsonl"

    if not manifest_path.exists():
        return ValidationReport(
            False, (ValidationIssue("missing_manifest", "book.json is missing"),)
        )
    manifest = _read_json(manifest_path)
    if not source_path.exists():
        issues.append(ValidationIssue("missing_source", "normalized source is missing"))
    if not index_path.exists():
        issues.append(ValidationIssue("missing_index", "chapter index is missing"))
    if issues:
        return ValidationReport(False, tuple(issues))

    source = source_path.read_text(encoding="utf-8")
    actual_hash = _sha256_text(source)
    expected_hash = (manifest.get("source") or {}).get("sha256")
    if actual_hash != expected_hash:
        issues.append(
            ValidationIssue(
                "source_hash_mismatch",
                "source/original.txt changed after indexing",
                "source/original.txt",
            )
        )

    try:
        rows = _read_jsonl(index_path)
    except WorkspaceError as exc:
        issues.append(ValidationIssue("invalid_index", str(exc), "index/chapters.jsonl"))
        return ValidationReport(False, tuple(issues))

    seen_ids: set[int] = set()
    previous_end = 0
    kind_counts = {"chapter": 0, "front_matter": 0}
    for row in rows:
        chapter_id = row.get("chapter_id")
        if not isinstance(chapter_id, int) or chapter_id in seen_ids:
            issues.append(
                ValidationIssue(
                    "invalid_chapter_id",
                    f"invalid or duplicate chapter_id: {chapter_id}",
                    "index/chapters.jsonl",
                )
            )
            continue
        seen_ids.add(chapter_id)
        if row.get("book_id") != book_id or row.get("source_sha256") != expected_hash:
            issues.append(ValidationIssue("index_identity_mismatch", f"chapter {chapter_id} index identity does not match the book", "index/chapters.jsonl"))
        kind = row.get("kind")
        if kind not in kind_counts:
            issues.append(ValidationIssue("invalid_chapter_kind", f"chapter {chapter_id} has invalid kind", "index/chapters.jsonl"))
        else:
            kind_counts[kind] += 1
        offsets = (
            row.get("char_start"),
            row.get("content_start"),
            row.get("content_end"),
            row.get("char_end"),
        )
        if not all(isinstance(value, int) for value in offsets) or not (
            0 <= offsets[0] <= offsets[1] <= offsets[2] <= offsets[3] <= len(source)
        ):
            issues.append(
                ValidationIssue(
                    "invalid_offsets",
                    f"chapter {chapter_id} has invalid source offsets",
                    "index/chapters.jsonl",
                )
            )
            continue
        if offsets[0] != previous_end:
            issues.append(ValidationIssue("noncontiguous_index", f"chapter {chapter_id} does not begin where the previous entry ended", "index/chapters.jsonl"))
        previous_end = offsets[3]
        content = source[offsets[1] : offsets[2]]
        if _sha256_text(content) != row.get("content_sha256"):
            issues.append(
                ValidationIssue(
                    "chapter_hash_mismatch",
                    f"chapter {chapter_id} content no longer matches its index",
                    "index/chapters.jsonl",
                )
            )

    if not rows or previous_end != len(source):
        issues.append(ValidationIssue("incomplete_index_coverage", "chapter index does not cover the complete source", "index/chapters.jsonl"))
    source_manifest = manifest.get("source") or {}
    if (
        kind_counts["chapter"] != source_manifest.get("chapter_count")
        or kind_counts["front_matter"] != source_manifest.get("front_matter_count")
    ):
        issues.append(ValidationIssue("manifest_count_mismatch", "manifest chapter counts do not match the index", "book.json"))

    return ValidationReport(not issues, tuple(issues))


def _find_occurrence(text: str, quote: str, occurrence: int) -> int:
    position = -1
    search_from = 0
    for _ in range(occurrence):
        position = text.find(quote, search_from)
        if position < 0:
            return -1
        search_from = position + len(quote)
    return position


def _fact_paths(book_dir: Path, part: str | None = None) -> list[Path]:
    facts_dir = book_dir / "facts/chapter_facts"
    if part is None:
        return sorted(facts_dir.glob("part-*.jsonl"))
    if not re.fullmatch(r"part-[0-9]{6}-[0-9]{6}\.jsonl", part):
        raise WorkspaceError("fact part must be a part-XXXXXX-XXXXXX.jsonl filename")
    path = facts_dir / part
    if not path.is_file():
        raise WorkspaceError(f"fact part does not exist: {part}")
    return [path]


def ground_fact_parts(root: Path, book_id: str, part: str | None = None) -> int:
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    source = (book_dir / "source/original.txt").read_text(encoding="utf-8")
    index = {
        row["chapter_id"]: row
        for row in _read_jsonl(book_dir / "index/chapters.jsonl")
    }
    fact_paths = _fact_paths(book_dir, part)
    grounded_by_path: dict[Path, list[dict[str, Any]]] = {}
    grounded_count = 0
    evidence_collections = (
        "events",
        "information_reveals",
        "state_changes",
        "clue_candidates",
    )

    for fact_path in fact_paths:
        rows = _read_jsonl(fact_path)
        for row in rows:
            chapter_id = row.get("chapter_id")
            if chapter_id not in index:
                raise WorkspaceError(
                    f"cannot ground unknown chapter {chapter_id} in {fact_path.name}"
                )
            chapter = index[chapter_id]
            content_start = chapter["content_start"]
            content = source[content_start : chapter["content_end"]]
            for field in evidence_collections:
                for record in row.get(field, []):
                    for evidence in record.get("evidence", []):
                        quote = evidence.get("quote")
                        occurrence = evidence.get("occurrence", 1)
                        if not isinstance(quote, str) or not quote:
                            raise WorkspaceError(
                                f"chapter {chapter_id} has empty evidence quote"
                            )
                        if not isinstance(occurrence, int) or occurrence < 1:
                            raise WorkspaceError(
                                f"chapter {chapter_id} has invalid evidence occurrence"
                            )
                        local_start = _find_occurrence(content, quote, occurrence)
                        if local_start < 0:
                            raise WorkspaceError(
                                f"chapter {chapter_id} evidence quote was not found: {quote!r}"
                            )
                        evidence["start"] = content_start + local_start
                        evidence["end"] = evidence["start"] + len(quote)
                        grounded_count += 1
            ending = row.get("ending_excerpt")
            if isinstance(ending, dict):
                quote = ending.get("quote")
                occurrence = ending.get("occurrence", 1)
                if not isinstance(quote, str) or not quote or not isinstance(occurrence, int) or occurrence < 1:
                    raise WorkspaceError(f"chapter {chapter_id} has invalid ending evidence")
                local_start = _find_occurrence(content, quote, occurrence)
                if local_start < 0:
                    raise WorkspaceError(
                        f"chapter {chapter_id} ending evidence was not found: {quote!r}"
                    )
                ending["start"] = content_start + local_start
                ending["end"] = ending["start"] + len(quote)
                grounded_count += 1
        grounded_by_path[fact_path] = rows

    for fact_path, rows in grounded_by_path.items():
        _write_jsonl(fact_path, rows)
    return grounded_count


def validate_fact_parts(
    root: Path,
    book_id: str,
    require_complete: bool = False,
    part: str | None = None,
) -> ValidationReport:
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    source_report = validate_book(root, book_id)
    if not source_report.valid:
        return source_report
    index_path = book_dir / "index/chapters.jsonl"
    if not index_path.exists():
        return ValidationReport(
            False,
            (ValidationIssue("missing_index", "chapter index is missing"),),
        )

    source = (book_dir / "source/original.txt").read_text(encoding="utf-8")
    index = {row["chapter_id"]: row for row in _read_jsonl(index_path)}
    fact_paths = _fact_paths(book_dir, part)
    issues: list[ValidationIssue] = []
    seen_chapters: set[int] = set()
    seen_fact_ids: set[str] = set()
    seen_fact_payloads: set[tuple[str, str]] = set()
    required_lists = (
        "events",
        "information_reveals",
        "state_changes",
        "clue_candidates",
        "unknowns",
    )
    evidence_collections = required_lists[:-1]

    for fact_path in fact_paths:
        try:
            rows = _read_jsonl(fact_path)
        except WorkspaceError as exc:
            issues.append(
                ValidationIssue("invalid_fact_jsonl", str(exc), str(fact_path.relative_to(book_dir)))
            )
            continue
        for row in rows:
            chapter_id = row.get("chapter_id")
            relative_path = str(fact_path.relative_to(book_dir))
            contract_errors = schema_errors(row, "chapter_fact.schema.json")
            if contract_errors:
                issues.append(
                    ValidationIssue(
                        "invalid_fact_schema",
                        f"chapter {chapter_id} violates chapter_fact.schema.json: {'; '.join(contract_errors[:3])}",
                        relative_path,
                    )
                )
            if not isinstance(chapter_id, int) or chapter_id not in index:
                issues.append(
                    ValidationIssue(
                        "unknown_fact_chapter",
                        f"fact row references unknown chapter_id: {chapter_id}",
                        relative_path,
                    )
                )
                continue
            if chapter_id in seen_chapters:
                issues.append(
                    ValidationIssue(
                        "duplicate_fact_chapter",
                        f"chapter {chapter_id} appears in more than one fact row",
                        relative_path,
                    )
                )
            seen_chapters.add(chapter_id)
            chapter = index[chapter_id]
            id_fields = (
                ("events", "event_id"),
                ("information_reveals", "reveal_id"),
                ("state_changes", "change_id"),
                ("clue_candidates", "candidate_id"),
            )
            expected_prefix = f"C{chapter_id:06d}-"
            for collection, id_field in id_fields:
                records = row.get(collection)
                if not isinstance(records, list):
                    continue
                for record in records:
                    identifier = record.get(id_field) if isinstance(record, dict) else None
                    if not isinstance(identifier, str):
                        continue
                    normalized_record = dict(record)
                    normalized_record.pop(id_field, None)
                    normalized_record.pop("confidence", None)

                    def compact(value: Any) -> Any:
                        if isinstance(value, dict):
                            return {
                                key: compact(child)
                                for key, child in value.items()
                                if child not in (None, "", [], {})
                            }
                        if isinstance(value, list):
                            return [compact(child) for child in value]
                        return value

                    normalized_record = compact(normalized_record)
                    signature = (
                        collection,
                        json.dumps(normalized_record, ensure_ascii=False, sort_keys=True),
                    )
                    if signature in seen_fact_payloads:
                        issues.append(
                            ValidationIssue(
                                "duplicate_fact_payload",
                                f"semantically duplicate {collection} record: {identifier}",
                                relative_path,
                            )
                        )
                    seen_fact_payloads.add(signature)
                    if not identifier.startswith(expected_prefix):
                        issues.append(
                            ValidationIssue(
                                "fact_id_chapter_mismatch",
                                f"fact ID {identifier} does not belong to chapter {chapter_id}",
                                relative_path,
                            )
                        )
                    if identifier in seen_fact_ids:
                        issues.append(
                            ValidationIssue(
                                "duplicate_fact_id",
                                f"fact ID appears more than once: {identifier}",
                                relative_path,
                            )
                        )
                    seen_fact_ids.add(identifier)
            if row.get("book_id") != book_id:
                issues.append(
                    ValidationIssue(
                        "fact_book_mismatch",
                        f"chapter {chapter_id} has the wrong book_id",
                        relative_path,
                    )
                )
            if row.get("source_sha256") != chapter.get("source_sha256"):
                issues.append(
                    ValidationIssue(
                        "fact_source_mismatch",
                        f"chapter {chapter_id} facts use a different source version",
                        relative_path,
                    )
                )
            for field in required_lists:
                if not isinstance(row.get(field), list):
                    issues.append(
                        ValidationIssue(
                            "invalid_fact_field",
                            f"chapter {chapter_id} field {field} must be an array",
                            relative_path,
                        )
                    )
            for field in evidence_collections:
                records = row.get(field)
                if not isinstance(records, list):
                    continue
                for record_number, record in enumerate(records, start=1):
                    if not isinstance(record, dict) or not isinstance(
                        record.get("evidence"), list
                    ) or not record["evidence"]:
                        issues.append(
                            ValidationIssue(
                                "missing_evidence",
                                f"chapter {chapter_id} {field} item {record_number} has no evidence",
                                relative_path,
                            )
                        )
                        continue
                    for evidence in record["evidence"]:
                        start = evidence.get("start") if isinstance(evidence, dict) else None
                        end = evidence.get("end") if isinstance(evidence, dict) else None
                        quote = evidence.get("quote") if isinstance(evidence, dict) else None
                        if not (
                            isinstance(start, int)
                            and isinstance(end, int)
                            and isinstance(quote, str)
                            and bool(quote)
                            and chapter["content_start"] <= start < end <= chapter["content_end"]
                        ):
                            issues.append(
                                ValidationIssue(
                                    "evidence_outside_chapter",
                                    f"chapter {chapter_id} {field} item {record_number} has invalid evidence offsets",
                                    relative_path,
                                )
                            )
                        elif source[start:end] != quote:
                            issues.append(
                                ValidationIssue(
                                    "evidence_text_mismatch",
                                    f"chapter {chapter_id} {field} item {record_number} quote does not match its offsets",
                                    relative_path,
                                )
                            )
            ending = row.get("ending_excerpt")
            if ending is not None:
                if not isinstance(ending, dict):
                    issues.append(ValidationIssue("invalid_ending_evidence", f"chapter {chapter_id} ending_excerpt must be an object", relative_path))
                else:
                    start, end, quote = ending.get("start"), ending.get("end"), ending.get("quote")
                    if not (
                        isinstance(start, int) and isinstance(end, int) and isinstance(quote, str)
                        and chapter["content_start"] <= start < end <= chapter["content_end"]
                        and source[start:end] == quote
                    ):
                        issues.append(ValidationIssue("invalid_ending_evidence", f"chapter {chapter_id} ending_excerpt is not grounded", relative_path))
    if require_complete and part is not None:
        issues.append(ValidationIssue("invalid_validation_scope", "--require-complete cannot be combined with --part"))
    elif require_complete:
        expected_chapters = {
            chapter_id
            for chapter_id, row in index.items()
            if row.get("kind") == "chapter"
        }
        missing = sorted(expected_chapters - seen_chapters)
        if missing:
            preview = ", ".join(str(chapter_id) for chapter_id in missing[:20])
            if len(missing) > 20:
                preview += f", ... ({len(missing)} total)"
            issues.append(
                ValidationIssue(
                    "missing_fact_chapters",
                    f"facts are missing for chapters: {preview}",
                    "facts/chapter_facts",
                )
            )
    return ValidationReport(not issues, tuple(issues))


def materialize_review_inputs(
    root: Path,
    book_id: str,
    run_id: str,
    part: str | None = None,
) -> int:
    _validate_run_id(run_id)
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    run_dir = book_dir / "runs" / run_id
    manifest_path = run_dir / "manifest.json"
    jobs_path = run_dir / "jobs/extract.jsonl"
    if not manifest_path.is_file() or not jobs_path.is_file():
        raise WorkspaceError(f"analysis run is incomplete or unknown: {run_id}")
    manifest = _read_json(manifest_path)
    jobs = {
        job.get("output_path"): job
        for job in _read_jsonl(jobs_path)
        if isinstance(job.get("output_path"), str)
    }
    rendered_inputs: list[tuple[Path, dict[str, Any]]] = []
    for fact_path in _fact_paths(book_dir, part):
        report = validate_fact_parts(root, book_id, part=fact_path.name)
        if not report.valid:
            first = report.issues[0]
            raise WorkspaceError(
                f"fact validation failed before review materialization: "
                f"{first.code}: {first.message}"
            )
        fact_relative = f"facts/chapter_facts/{fact_path.name}"
        job = jobs.get(fact_relative)
        if job is None:
            raise WorkspaceError(
                f"fact part does not belong to run {run_id}: {fact_path.name}"
            )
        source_input = _read_json(book_dir / job["input_path"])
        facts = _read_jsonl(fact_path)
        source_chapters = source_input.get("chapters")
        if (
            not isinstance(source_chapters, list)
            or len(source_chapters) != len(facts)
            or [chapter.get("chapter_id") for chapter in source_chapters]
            != [fact.get("chapter_id") for fact in facts]
        ):
            raise WorkspaceError(
                f"fact chapters do not match materialized source: {fact_path.name}"
            )
        review_name = fact_path.name.replace("part-", "review-", 1).replace(
            ".jsonl", ".json"
        )
        rendered_inputs.append(
            (
                run_dir / "inputs" / review_name,
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "book_id": book_id,
                    "source_sha256": manifest["source_sha256"],
                    "fact_path": fact_relative,
                    "fact_sha256": hashlib.sha256(fact_path.read_bytes()).hexdigest(),
                    "chapters": [
                        {"source": source_chapter, "facts": fact}
                        for source_chapter, fact in zip(source_chapters, facts)
                    ],
                },
            )
        )
    for input_path, payload in rendered_inputs:
        _write_json(input_path, payload)
    return len(rendered_inputs)


def _valid_analysis_report(path: Path) -> bool:
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    expected_heading = {
        "characters.md": "# Characters",
        "pacing.md": "# Pacing",
        "hooks.md": "# Hooks",
        "payoffs.md": "# Payoffs",
    }.get(path.name)
    return (
        len(content.strip()) >= 500
        and expected_heading is not None
        and expected_heading in content
        and FACT_REFERENCE_PATTERN.search(content) is not None
    )


def _valid_book_dna(path: Path) -> bool:
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    required_headings = (
        "# Book DNA",
        "## Evidence scope",
        "## Reading drivers",
        "## Narrative loops",
        "## Payoff and reward system",
        "## Character behavior rules",
        "## Pacing model",
        "## Hooks and information control",
        "## Foreshadowing model",
        "## Craft hypotheses",
        "## Transfer boundary",
    )
    return (
        len(content.strip()) >= 1500
        and all(heading in content for heading in required_headings)
        and ARC_REFERENCE_PATTERN.search(content) is not None
    )


def workspace_status(root: Path, book_id: str) -> dict[str, Any]:
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    manifest = _read_json(book_dir / "book.json")
    facts = list((book_dir / "facts/chapter_facts").glob("part-*.jsonl"))
    run_dirs = sorted(
        path for path in (book_dir / "runs").iterdir()
        if path.is_dir() and path.name != "_recovery" and (path / "manifest.json").is_file()
    )
    run_progress = []
    for run_dir in run_dirs:
        jobs_path = run_dir / "jobs/extract.jsonl"
        jobs = _read_jsonl(jobs_path) if jobs_path.exists() else []
        completed_jobs = 0
        invalid_jobs = 0
        run_manifest = _read_json(run_dir / "manifest.json")
        for job in jobs:
            output_path = book_dir / job["output_path"]
            if not output_path.is_file() or output_path.stat().st_size == 0:
                continue
            try:
                rows = _read_jsonl(output_path)
            except WorkspaceError:
                invalid_jobs += 1
                continue
            expected = set(job["chapter_ids"])
            actual = {row.get("chapter_id") for row in rows}
            required_lists = (
                "events",
                "information_reveals",
                "state_changes",
                "clue_candidates",
                "unknowns",
            )
            valid_rows = all(
                row.get("schema_version") == SCHEMA_VERSION
                and row.get("book_id") == book_id
                and row.get("source_sha256") == run_manifest.get("source_sha256")
                and all(isinstance(row.get(field), list) for field in required_lists)
                and not schema_errors(row, "chapter_fact.schema.json")
                for row in rows
            )
            if rows and len(rows) == len(expected) and actual == expected and valid_rows:
                completed_jobs += 1
            else:
                invalid_jobs += 1
        extract_progress = {
            "job_count": len(jobs),
            "completed_jobs": completed_jobs,
            "invalid_jobs": invalid_jobs,
            "pending_jobs": len(jobs) - completed_jobs - invalid_jobs,
        }
        extraction_files_complete = (
            bool(jobs) and completed_jobs == len(jobs) and invalid_jobs == 0
        )
        fact_gate_valid = False
        if extraction_files_complete:
            fact_gate_valid = validate_fact_parts(
                root, book_id, require_complete=True
            ).valid
        extract_progress["gate_valid"] = fact_gate_valid

        link_jobs_path = run_dir / "jobs/link.jsonl"
        link_jobs = _read_jsonl(link_jobs_path) if link_jobs_path.exists() else []
        linked_jobs = 0
        invalid_link_jobs = 0
        for job in link_jobs:
            output_path = book_dir / job["output_path"]
            if not output_path.is_file() or output_path.stat().st_size == 0:
                continue
            try:
                rows = _read_jsonl(output_path)
            except WorkspaceError:
                invalid_link_jobs += 1
                continue
            expected = set(job["chapter_ids"])
            actual = {row.get("chapter_id") for row in rows}
            valid_rows = all(
                row.get("book_id") == book_id
                and not schema_errors(row, "chapter_annotation.schema.json")
                for row in rows
            )
            if rows and len(rows) == len(expected) and actual == expected and valid_rows:
                linked_jobs += 1
            else:
                invalid_link_jobs += 1
        link_progress = {
            "job_count": len(link_jobs),
            "completed_jobs": linked_jobs,
            "invalid_jobs": invalid_link_jobs,
            "pending_jobs": len(link_jobs) - linked_jobs - invalid_link_jobs,
        }

        arc_rows = _read_jsonl(book_dir / "arcs/arcs.jsonl")
        link_ready = (
            fact_gate_valid
            and bool(link_jobs)
            and linked_jobs == len(link_jobs)
            and invalid_link_jobs == 0
        )
        structure_valid = False
        if link_ready:
            from .structure import validate_structure

            structure_valid = validate_structure(root, book_id, require_complete=True).valid
        link_progress["gate_valid"] = structure_valid
        required_analysis_reports = (
            "analysis/characters.md",
            "analysis/pacing.md",
            "analysis/hooks.md",
            "analysis/payoffs.md",
        )
        analysis_reports_valid = all(
            _valid_analysis_report(book_dir / relative_path)
            for relative_path in required_analysis_reports
        )
        if not structure_valid:
            analyze_status = "blocked"
        elif arc_rows and all(
            arc.get("book_id") == book_id
            and not schema_errors(arc, "arc_analysis.schema.json")
            for arc in arc_rows
        ) and analysis_reports_valid:
            analyze_status = "completed"
        elif arc_rows:
            analyze_status = "invalid"
        else:
            analyze_status = "pending"

        book_dna = book_dir / "distilled/book_dna.md"
        if analyze_status != "completed":
            distill_status = "blocked"
        elif _valid_book_dna(book_dna):
            distill_status = "completed"
        else:
            distill_status = "pending"
        run_progress.append(
            {
                "run_id": run_dir.name,
                "job_count": len(jobs),
                "completed_jobs": completed_jobs,
                "invalid_jobs": invalid_jobs,
                "pending_jobs": len(jobs) - completed_jobs - invalid_jobs,
                "stages": {
                    "extract": extract_progress,
                    "validate": {
                        "status": (
                            "completed"
                            if fact_gate_valid
                            else "failed"
                            if extraction_files_complete
                            else "blocked"
                        )
                    },
                    "link": link_progress,
                    "analyze": {"status": analyze_status},
                    "distill": {"status": distill_status},
                },
            }
        )
    return {
        "book_id": book_id,
        "title": manifest["title"],
        "status": manifest["status"],
        "source": manifest.get("source"),
        "fact_part_count": len(facts),
        "annotation_part_count": len(
            list((book_dir / "facts/chapter_annotations").glob("part-*.jsonl"))
        ),
        "entity_count": len(_read_jsonl(book_dir / "index/entities.jsonl")),
        "ledger_event_counts": {
            "state": len(_read_jsonl(book_dir / "ledgers/state_events.jsonl")),
            "state_checkpoints": len(
                _read_jsonl(book_dir / "ledgers/state_checkpoints.jsonl")
            ),
            "thread": len(_read_jsonl(book_dir / "ledgers/thread_events.jsonl")),
            "clue": len(_read_jsonl(book_dir / "ledgers/clue_events.jsonl")),
        },
        "runs": [run_dir.name for run_dir in run_dirs],
        "run_progress": run_progress,
    }


def chapter_batch(root: Path, book_id: str, start: int, end: int) -> dict[str, Any]:
    if start < 0 or end < start:
        raise WorkspaceError("invalid chapter range")
    if end - start + 1 > 50:
        raise WorkspaceError("chapter batch cannot exceed 50 chapters")
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    source = (book_dir / "source/original.txt").read_text(encoding="utf-8")
    rows = _read_jsonl(book_dir / "index/chapters.jsonl")
    selected = []
    for row in rows:
        if start <= row["chapter_id"] <= end:
            item = dict(row)
            item["raw_text"] = source[row["char_start"] : row["char_end"]]
            selected.append(item)
    if not selected:
        raise WorkspaceError(f"no chapters found in range {start}-{end}")
    total_chars = sum(len(item["raw_text"]) for item in selected)
    if total_chars > 500_000:
        raise WorkspaceError("chapter batch cannot exceed 500000 characters")
    return {"book_id": book_id, "start_chapter": start, "end_chapter": end, "chapters": selected}
