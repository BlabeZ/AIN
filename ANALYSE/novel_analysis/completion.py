from __future__ import annotations

import hashlib
from pathlib import Path

from .structure import fact_id_chapters, validate_structure
from .workspace import (
    ValidationIssue,
    ValidationReport,
    WorkspaceError,
    ARC_REFERENCE_PATTERN,
    FACT_REFERENCE_PATTERN,
    _book_dir,
    _materialized_text_chunks,
    _read_json,
    _read_jsonl,
    _reject_symlinks,
    _utc_now,
    _valid_analysis_report,
    _valid_book_dna,
    _write_json,
    validate_book,
    workspace_status,
)


REQUIRED_ANALYSIS_REPORTS = (
    "analysis/characters.md",
    "analysis/pacing.md",
    "analysis/hooks.md",
    "analysis/payoffs.md",
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _completion_artifact_paths(book_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    fixed_files = (
        "index/chapters.jsonl",
        "index/entities.jsonl",
        "ledgers/state_events.jsonl",
        "ledgers/state_checkpoints.jsonl",
        "ledgers/thread_events.jsonl",
        "ledgers/clue_events.jsonl",
        "arcs/arcs.jsonl",
        "distilled/book_dna.md",
    ) + REQUIRED_ANALYSIS_REPORTS
    for relative_path in fixed_files:
        path = book_dir / relative_path
        if path.is_file():
            paths[relative_path] = path
    for pattern in (
        "facts/chapter_facts/part-*.jsonl",
        "facts/chapter_annotations/part-*.jsonl",
        "runs/*/manifest.json",
        "runs/*/jobs/*.jsonl",
        "runs/*/inputs/*.json",
        "runs/*/reports/**/*",
    ):
        for path in sorted(book_dir.glob(pattern)):
            if path.is_file():
                paths[str(path.relative_to(book_dir))] = path
    return paths


def _validate_run_integrity(root: Path, book_id: str) -> ValidationReport:
    book_dir = _book_dir(root, book_id)
    issues: list[ValidationIssue] = []
    run_dirs = [
        path
        for path in (book_dir / "runs").iterdir()
        if path.is_dir() and path.name != "_recovery"
    ]
    if len(run_dirs) != 1:
        return ValidationReport(
            False,
            (ValidationIssue("invalid_run_count", "exactly one complete analysis run is required"),),
        )
    run_dir = run_dirs[0]
    try:
        manifest = _read_json(run_dir / "manifest.json")
        extract_jobs = _read_jsonl(run_dir / "jobs/extract.jsonl")
        link_jobs = _read_jsonl(run_dir / "jobs/link.jsonl")
        analyze_jobs = _read_jsonl(run_dir / "jobs/analyze.jsonl")
        distill_jobs = _read_jsonl(run_dir / "jobs/distill.jsonl")
    except (OSError, ValueError) as exc:
        return ValidationReport(
            False,
            (ValidationIssue("invalid_run_files", f"run files are missing or malformed: {exc}"),),
        )
    book_manifest = _read_json(book_dir / "book.json")
    source_hash = (book_manifest.get("source") or {}).get("sha256")
    expected_job_counts = {
        "extract": len(extract_jobs),
        "link": len(link_jobs),
        "analyze": 1,
        "distill": 1,
    }
    expected_manifest_keys = {
        "schema_version", "run_id", "book_id", "source_sha256", "status",
        "batch_size", "job_count", "job_counts", "created_at", "stages",
    }
    if (
        set(manifest) != expected_manifest_keys
        or not isinstance(manifest.get("created_at"), str)
        or
        manifest.get("schema_version") != "1.0.0"
        or
        manifest.get("run_id") != run_dir.name
        or manifest.get("book_id") != book_id
        or manifest.get("source_sha256") != source_hash
        or manifest.get("status") != "planned"
        or not isinstance(manifest.get("batch_size"), int)
        or not 1 <= manifest["batch_size"] <= 50
        or manifest.get("job_count") != len(extract_jobs)
        or manifest.get("job_counts") != expected_job_counts
        or manifest.get("stages") != ["extract", "validate", "link", "analyze", "distill"]
    ):
        issues.append(ValidationIssue("run_identity_mismatch", "run manifest identity or counts are invalid"))

    indexed_rows = _read_jsonl(book_dir / "index/chapters.jsonl")
    indexed = {
        row["chapter_id"]: row
        for row in indexed_rows
        if row.get("kind") == "chapter"
    }
    source = (book_dir / "source/original.txt").read_text(encoding="utf-8")
    planned_chapters: list[int] = []
    seen_job_ids: set[str] = set()
    input_paths: set[str] = set()
    extract_by_id: dict[str, dict] = {}
    for job in extract_jobs:
        job_id = job.get("job_id")
        chapter_ids = job.get("chapter_ids")
        if not isinstance(job_id, str) or job_id in seen_job_ids:
            issues.append(ValidationIssue("invalid_extract_job_id", "extract job IDs must be unique"))
            continue
        seen_job_ids.add(job_id)
        extract_by_id[job_id] = job
        if not isinstance(chapter_ids, list) or not chapter_ids or not all(isinstance(item, int) for item in chapter_ids):
            issues.append(ValidationIssue("invalid_extract_job_chapters", f"job {job_id} has invalid chapters"))
            continue
        planned_chapters.extend(chapter_ids)
        canonical_job_id = f"extract-{chapter_ids[0]:06d}-{chapter_ids[-1]:06d}"
        if job_id != canonical_job_id:
            issues.append(
                ValidationIssue(
                    "noncanonical_extract_job_id",
                    f"extract job ID does not match its chapter range: {job_id}",
                )
            )
        expected_output = f"facts/chapter_facts/part-{chapter_ids[0]:06d}-{chapter_ids[-1]:06d}.jsonl"
        expected_input = f"runs/{run_dir.name}/inputs/{canonical_job_id}.json"
        expected_job = {
            "schema_version": "1.0.0",
            "job_id": canonical_job_id,
            "stage": "extract",
            "status": "pending",
            "book_id": book_id,
            "chapter_ids": chapter_ids,
            "start_chapter": chapter_ids[0],
            "end_chapter": chapter_ids[-1],
            "output_path": expected_output,
            "input_path": expected_input,
        }
        if job != expected_job:
            issues.append(ValidationIssue("invalid_extract_job_path", f"job {job_id} has a noncanonical path"))
        if expected_input in input_paths:
            issues.append(ValidationIssue("duplicate_job_input_path", f"job input path is duplicated: {expected_input}"))
        input_paths.add(expected_input)
        input_path = book_dir / expected_input
        if not input_path.is_file():
            issues.append(ValidationIssue("missing_materialized_input", f"job input is missing: {job_id}"))
            continue
        payload = _read_json(input_path)
        payload_chapters = payload.get("chapters")
        if (
            set(payload) != {
                "schema_version", "run_id", "job_id", "book_id", "source_sha256",
                "output_path", "chapters",
            }
            or payload.get("schema_version") != "1.0.0"
            or
            payload.get("run_id") != run_dir.name
            or payload.get("job_id") != job_id
            or payload.get("book_id") != book_id
            or payload.get("source_sha256") != source_hash
            or payload.get("output_path") != expected_output
            or not isinstance(payload_chapters, list)
            or [row.get("chapter_id") for row in payload_chapters] != chapter_ids
        ):
            issues.append(ValidationIssue("invalid_materialized_input", f"job input identity is invalid: {job_id}"))
            continue
        for chapter in payload_chapters:
            indexed_chapter = indexed.get(chapter.get("chapter_id"))
            expected_chapter = dict(indexed_chapter) if indexed_chapter is not None else None
            if expected_chapter is not None:
                chapter_text = source[
                    indexed_chapter["char_start"] : indexed_chapter["char_end"]
                ]
                expected_chapter["raw_text_chunks"] = _materialized_text_chunks(
                    chapter_text
                )
            if indexed_chapter is None or chapter != expected_chapter:
                issues.append(ValidationIssue("materialized_input_text_mismatch", f"job input text is stale: {job_id}"))
                break
        fact_path = book_dir / expected_output
        review_name = (
            canonical_job_id.replace("extract-", "review-", 1) + ".json"
        )
        review_path = run_dir / "inputs" / review_name
        if not fact_path.is_file() or not review_path.is_file():
            issues.append(
                ValidationIssue(
                    "missing_review_input",
                    f"validated review input is missing: {job_id}",
                )
            )
            continue
        fact_rows = _read_jsonl(fact_path)
        expected_review = {
            "schema_version": "1.0.0",
            "run_id": run_dir.name,
            "book_id": book_id,
            "source_sha256": source_hash,
            "fact_path": expected_output,
            "fact_sha256": _file_sha256(fact_path),
            "chapters": [
                {"source": chapter, "facts": fact}
                for chapter, fact in zip(payload_chapters, fact_rows)
            ],
        }
        if (
            len(payload_chapters) != len(fact_rows)
            or _read_json(review_path) != expected_review
        ):
            issues.append(
                ValidationIssue(
                    "stale_review_input",
                    f"review input does not match current source and facts: {job_id}",
                )
            )
    if planned_chapters != sorted(indexed) or len(planned_chapters) != len(set(planned_chapters)):
        issues.append(ValidationIssue("run_chapter_coverage_mismatch", "extract jobs do not cover indexed chapters exactly once"))

    expected_link_ids = {job_id.replace("extract-", "link-") for job_id in seen_job_ids}
    if {job.get("job_id") for job in link_jobs} != expected_link_ids or len(link_jobs) != len(expected_link_ids):
        issues.append(ValidationIssue("link_job_mismatch", "link jobs do not correspond to extract jobs"))
    for link_job in link_jobs:
        link_id = link_job.get("job_id")
        extract_id = link_id.replace("link-", "extract-") if isinstance(link_id, str) else ""
        extract_job = extract_by_id.get(extract_id)
        if extract_job is None:
            continue
        expected_link = {
            **extract_job,
            "job_id": link_id,
            "stage": "link",
            "input_path": extract_job["output_path"],
            "output_path": extract_job["output_path"].replace(
                "facts/chapter_facts/", "facts/chapter_annotations/"
            ),
        }
        if link_job != expected_link:
            issues.append(ValidationIssue("invalid_link_job", f"link job contract is invalid: {link_id}"))
    expected_analyze_outputs = {
        "arcs/arcs.jsonl",
        "analysis/characters.md",
        "analysis/pacing.md",
        "analysis/hooks.md",
        "analysis/payoffs.md",
    }
    expected_analyze_job = {
        "schema_version": "1.0.0",
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
    if (
        len(analyze_jobs) != 1
        or analyze_jobs[0] != expected_analyze_job
    ):
        issues.append(ValidationIssue("invalid_analyze_job", "analyze job contract is invalid"))
    expected_distill_job = {
        "schema_version": "1.0.0",
        "job_id": "distill-book",
        "stage": "distill",
        "status": "blocked",
        "book_id": book_id,
        "depends_on": ["analyze-book"],
        "output_path": "distilled/book_dna.md",
    }
    if (
        len(distill_jobs) != 1
        or distill_jobs[0] != expected_distill_job
    ):
        issues.append(ValidationIssue("invalid_distill_job", "distill job contract is invalid"))

    for fact_path in sorted((book_dir / "facts/chapter_facts").glob("part-*.jsonl")):
        for fact in _read_jsonl(fact_path):
            extractor = fact.get("extractor")
            if (
                not isinstance(extractor, dict)
                or extractor.get("agent") != "novel-extractor"
                or extractor.get("run_id") != run_dir.name
                or not extractor.get("prompt_version")
            ):
                issues.append(
                    ValidationIssue(
                        "invalid_extractor_provenance",
                        f"fact row has invalid extractor provenance: {fact_path.name}",
                        str(fact_path.relative_to(book_dir)),
                    )
                )
    return ValidationReport(not issues, tuple(issues))


def validate_distilled_book(root: Path, book_id: str) -> ValidationReport:
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    issues: list[ValidationIssue] = []
    issues.extend(validate_book(root, book_id).issues)
    issues.extend(validate_structure(root, book_id, require_complete=True).issues)
    fact_ids = set(fact_id_chapters(book_dir))
    run_report = _validate_run_integrity(root, book_id)
    issues.extend(run_report.issues)
    if not run_report.valid:
        return ValidationReport(False, tuple(issues))
    for relative_path in REQUIRED_ANALYSIS_REPORTS:
        report_path = book_dir / relative_path
        if not _valid_analysis_report(report_path):
            issues.append(
                ValidationIssue(
                    "missing_analysis_report",
                    f"analysis report is missing or too short: {relative_path}",
                    relative_path,
                )
            )
        else:
            referenced_ids = set(
                FACT_REFERENCE_PATTERN.findall(report_path.read_text(encoding="utf-8"))
            )
            unknown_ids = sorted(referenced_ids - fact_ids)
            if not referenced_ids or unknown_ids:
                issues.append(
                    ValidationIssue(
                        "invalid_analysis_evidence",
                        f"analysis report has missing or unknown fact references: {relative_path}",
                        relative_path,
                    )
                )
    if not _valid_book_dna(book_dir / "distilled/book_dna.md"):
        issues.append(
            ValidationIssue(
                "invalid_book_dna",
                "Book DNA is missing required sections or is too short",
                "distilled/book_dna.md",
            )
        )
    else:
        dna_content = (book_dir / "distilled/book_dna.md").read_text(encoding="utf-8")
        dna_arc_ids = set(ARC_REFERENCE_PATTERN.findall(dna_content))
        actual_arc_ids = {
            arc.get("arc_id")
            for arc in _read_jsonl(book_dir / "arcs/arcs.jsonl")
            if isinstance(arc.get("arc_id"), str)
        }
        if not dna_arc_ids or not dna_arc_ids.issubset(actual_arc_ids):
            issues.append(
                ValidationIssue(
                    "invalid_book_dna_evidence",
                    "Book DNA references missing or unknown arc IDs",
                    "distilled/book_dna.md",
                )
            )
    status = workspace_status(root, book_id)
    if not status["run_progress"]:
        issues.append(ValidationIssue("missing_analysis_run", "book has no analysis run"))
    else:
        stages = status["run_progress"][0]["stages"]
        if stages["analyze"]["status"] != "completed":
            issues.append(ValidationIssue("analysis_not_complete", "analysis stage is not complete"))
        if stages["distill"]["status"] != "completed":
            issues.append(ValidationIssue("distillation_not_complete", "distillation stage is not complete"))
    return ValidationReport(not issues, tuple(issues))


def finalize_book(root: Path, book_id: str) -> Path:
    report = validate_distilled_book(root, book_id)
    if not report.valid:
        first = report.issues[0]
        raise WorkspaceError(f"cannot finalize book: {first.code}: {first.message}")
    book_dir = _book_dir(root, book_id)
    status = workspace_status(root, book_id)
    manifest = _read_json(book_dir / "book.json")
    artifacts = {
        relative_path: _file_sha256(path)
        for relative_path, path in _completion_artifact_paths(book_dir).items()
    }
    completion = {
        "schema_version": "1.0.0",
        "book_id": book_id,
        "source_sha256": manifest["source"]["sha256"],
        "run_id": status["run_progress"][0]["run_id"],
        "artifacts": artifacts,
        "completed_at": _utc_now(),
    }
    completion_path = book_dir / "distilled/completion.json"
    _write_json(completion_path, completion)
    manifest["status"] = "distilled"
    manifest["updated_at"] = _utc_now()
    _write_json(book_dir / "book.json", manifest)
    return completion_path


def validate_completion_manifest(root: Path, book_id: str) -> ValidationReport:
    book_dir = _book_dir(root, book_id)
    completion_path = book_dir / "distilled/completion.json"
    if not completion_path.is_file():
        return ValidationReport(False, (ValidationIssue("missing_completion", "completion manifest is missing"),))
    issues = list(validate_distilled_book(root, book_id).issues)
    completion = _read_json(completion_path)
    manifest = _read_json(book_dir / "book.json")
    if (
        completion.get("book_id") != book_id
        or completion.get("source_sha256") != (manifest.get("source") or {}).get("sha256")
        or manifest.get("status") != "distilled"
    ):
        issues.append(ValidationIssue("completion_identity_mismatch", "completion identity does not match the book"))
    artifacts = completion.get("artifacts")
    expected_paths = _completion_artifact_paths(book_dir)
    if not isinstance(artifacts, dict):
        issues.append(ValidationIssue("invalid_completion_artifacts", "completion artifacts must be an object"))
        artifacts = {}
    if set(artifacts) != set(expected_paths):
        issues.append(
            ValidationIssue(
                "completion_artifact_set_mismatch",
                "completion artifact set is incomplete or contains unexpected paths",
            )
        )
    for relative_path, expected_hash in artifacts.items():
        artifact = (book_dir / relative_path).resolve()
        if (
            not artifact.is_relative_to(book_dir)
            or not artifact.is_file()
            or _file_sha256(artifact) != expected_hash
        ):
            issues.append(
                ValidationIssue(
                    "completion_artifact_mismatch",
                    f"completion artifact is missing or changed: {relative_path}",
                    relative_path,
                )
            )
    return ValidationReport(not issues, tuple(issues))
