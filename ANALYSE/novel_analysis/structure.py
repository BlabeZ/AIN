from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schema_validation import schema_errors
from .workspace import (
    ValidationIssue,
    ValidationReport,
    _book_dir,
    _read_jsonl,
    _reject_symlinks,
    validate_book,
    validate_fact_parts,
)


LEDGER_FILES = (
    ("ledgers/state_events.jsonl", "state"),
    ("ledgers/state_checkpoints.jsonl", "state"),
    ("ledgers/thread_events.jsonl", "thread"),
    ("ledgers/clue_events.jsonl", "clue"),
)


def _fact_ids(book_dir: Path) -> tuple[set[str], set[int]]:
    identifiers: set[str] = set()
    chapters: set[int] = set()
    id_fields = (
        ("events", "event_id"),
        ("information_reveals", "reveal_id"),
        ("state_changes", "change_id"),
        ("clue_candidates", "candidate_id"),
    )
    for fact_path in sorted((book_dir / "facts/chapter_facts").glob("part-*.jsonl")):
        for row in _read_jsonl(fact_path):
            chapter_id = row.get("chapter_id")
            if isinstance(chapter_id, int):
                chapters.add(chapter_id)
            for collection, id_field in id_fields:
                for record in row.get(collection, []):
                    identifier = record.get(id_field) if isinstance(record, dict) else None
                    if isinstance(identifier, str):
                        identifiers.add(identifier)
    return identifiers, chapters


def fact_id_chapters(book_dir: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    id_fields = (
        ("events", "event_id"),
        ("information_reveals", "reveal_id"),
        ("state_changes", "change_id"),
        ("clue_candidates", "candidate_id"),
    )
    for fact_path in sorted((book_dir / "facts/chapter_facts").glob("part-*.jsonl")):
        for row in _read_jsonl(fact_path):
            chapter_id = row.get("chapter_id")
            if not isinstance(chapter_id, int):
                continue
            for collection, id_field in id_fields:
                for record in row.get(collection, []):
                    identifier = record.get(id_field) if isinstance(record, dict) else None
                    if isinstance(identifier, str):
                        result[identifier] = chapter_id
    return result


def _collect_entity_ids(value: Any, identifiers: set[str]) -> None:
    if isinstance(value, dict):
        entity_id = value.get("entity_id")
        if isinstance(entity_id, str):
            identifiers.add(entity_id)
        for child in value.values():
            _collect_entity_ids(child, identifiers)
    elif isinstance(value, list):
        for child in value:
            _collect_entity_ids(child, identifiers)


def _fact_link_requirements(book_dir: Path) -> tuple[set[str], set[str], set[str]]:
    entity_ids: set[str] = set()
    state_change_ids: set[str] = set()
    clue_candidate_ids: set[str] = set()
    for fact_path in sorted((book_dir / "facts/chapter_facts").glob("part-*.jsonl")):
        for row in _read_jsonl(fact_path):
            _collect_entity_ids(row, entity_ids)
            state_change_ids.update(
                record["change_id"]
                for record in row.get("state_changes", [])
                if isinstance(record, dict) and isinstance(record.get("change_id"), str)
            )
            clue_candidate_ids.update(
                record["candidate_id"]
                for record in row.get("clue_candidates", [])
                if isinstance(record, dict) and isinstance(record.get("candidate_id"), str)
            )
    return entity_ids, state_change_ids, clue_candidate_ids


def _annotation_fact_refs(annotation: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for conflict in annotation.get("conflicts", []):
        if isinstance(conflict, dict):
            references.update(
                item for item in conflict.get("event_ids", []) if isinstance(item, str)
            )
    hook = annotation.get("hook")
    if isinstance(hook, dict):
        references.update(
            item for item in hook.get("evidence_event_ids", []) if isinstance(item, str)
        )
    return references


def validate_structure(
    root: Path, book_id: str, require_complete: bool = False
) -> ValidationReport:
    book_dir = _book_dir(root, book_id)
    _reject_symlinks(book_dir)
    issues: list[ValidationIssue] = []
    source_report = validate_book(root, book_id)
    issues.extend(source_report.issues)
    fact_report = validate_fact_parts(
        root, book_id, require_complete=require_complete
    )
    issues.extend(fact_report.issues)
    if not source_report.valid or not fact_report.valid:
        return ValidationReport(False, tuple(issues))
    fact_ids, fact_chapters = _fact_ids(book_dir)
    fact_chapter_map = fact_id_chapters(book_dir)
    fact_entity_ids, state_change_ids, clue_candidate_ids = _fact_link_requirements(
        book_dir
    )

    entity_path = book_dir / "index/entities.jsonl"
    entities = _read_jsonl(entity_path)
    entity_ids: set[str] = set()
    for row_number, entity in enumerate(entities, start=1):
        errors = schema_errors(entity, "entity.schema.json")
        if errors:
            issues.append(
                ValidationIssue(
                    "invalid_entity_schema",
                    f"entity row {row_number}: {'; '.join(errors[:3])}",
                    "index/entities.jsonl",
                )
            )
        if entity.get("book_id") != book_id:
            issues.append(
                ValidationIssue(
                    "entity_book_mismatch",
                    f"entity row {row_number} belongs to another book",
                    "index/entities.jsonl",
                )
            )
        entity_id = entity.get("entity_id")
        if isinstance(entity_id, str):
            if entity_id in entity_ids:
                issues.append(
                    ValidationIssue(
                        "duplicate_entity_id",
                        f"duplicate entity_id: {entity_id}",
                        "index/entities.jsonl",
                    )
                )
            entity_ids.add(entity_id)

    unknown_fact_entities = sorted(fact_entity_ids - entity_ids)
    if unknown_fact_entities:
        issues.append(
            ValidationIssue(
                "unknown_fact_entity",
                f"facts reference unregistered entities: {', '.join(unknown_fact_entities[:20])}",
                "index/entities.jsonl",
            )
        )

    seen_annotation_chapters: set[int] = set()
    annotation_paths = sorted(
        (book_dir / "facts/chapter_annotations").glob("part-*.jsonl")
    )
    for annotation_path in annotation_paths:
        relative_path = str(annotation_path.relative_to(book_dir))
        for annotation in _read_jsonl(annotation_path):
            chapter_id = annotation.get("chapter_id")
            errors = schema_errors(annotation, "chapter_annotation.schema.json")
            if errors:
                issues.append(
                    ValidationIssue(
                        "invalid_annotation_schema",
                        f"chapter {chapter_id}: {'; '.join(errors[:3])}",
                        relative_path,
                    )
                )
            if annotation.get("book_id") != book_id:
                issues.append(
                    ValidationIssue(
                        "annotation_book_mismatch",
                        f"chapter {chapter_id} belongs to another book",
                        relative_path,
                    )
                )
            if not isinstance(chapter_id, int) or chapter_id not in fact_chapters:
                issues.append(
                    ValidationIssue(
                        "unknown_annotation_chapter",
                        f"annotation references unknown fact chapter: {chapter_id}",
                        relative_path,
                    )
                )
                continue
            if chapter_id in seen_annotation_chapters:
                issues.append(
                    ValidationIssue(
                        "duplicate_annotation_chapter",
                        f"chapter {chapter_id} has duplicate annotations",
                        relative_path,
                    )
                )
            seen_annotation_chapters.add(chapter_id)
            unknown_refs = sorted(_annotation_fact_refs(annotation) - fact_ids)
            if unknown_refs:
                issues.append(
                    ValidationIssue(
                        "unknown_annotation_fact",
                        f"chapter {chapter_id} references unknown facts: {', '.join(unknown_refs)}",
                        relative_path,
                    )
                )

    ledger_fact_refs = {"state": set(), "thread": set(), "clue": set()}
    thread_ids: set[str] = set()
    ledger_event_ids: set[str] = set()
    for relative_path, expected_type in LEDGER_FILES:
        ledger_path = book_dir / relative_path
        for row_number, event in enumerate(_read_jsonl(ledger_path), start=1):
            errors = schema_errors(event, "ledger_event.schema.json")
            if errors:
                issues.append(
                    ValidationIssue(
                        "invalid_ledger_schema",
                        f"ledger row {row_number}: {'; '.join(errors[:3])}",
                        relative_path,
                    )
                )
            if event.get("book_id") != book_id or event.get("ledger_type") != expected_type:
                issues.append(
                    ValidationIssue(
                        "ledger_identity_mismatch",
                        f"ledger row {row_number} has the wrong book or ledger type",
                        relative_path,
                    )
                )
            ledger_event_id = event.get("event_id")
            if isinstance(ledger_event_id, str):
                if ledger_event_id in ledger_event_ids:
                    issues.append(
                        ValidationIssue(
                            "duplicate_ledger_event_id",
                            f"duplicate ledger event ID: {ledger_event_id}",
                            relative_path,
                        )
                    )
                ledger_event_ids.add(ledger_event_id)
            if event.get("chapter_id") not in fact_chapters:
                issues.append(
                    ValidationIssue(
                        "unknown_ledger_chapter",
                        f"ledger row {row_number} references an unknown chapter",
                        relative_path,
                    )
                )
            if (
                relative_path.endswith("state_checkpoints.jsonl")
                and event.get("operation") != "checkpoint"
            ):
                issues.append(
                    ValidationIssue(
                        "invalid_state_checkpoint",
                        f"checkpoint row {row_number} must use operation=checkpoint",
                        relative_path,
                    )
                )
            unknown_refs = sorted(
                set(event.get("source_fact_ids", [])) - fact_ids
                if isinstance(event.get("source_fact_ids"), list)
                else set()
            )
            if unknown_refs:
                issues.append(
                    ValidationIssue(
                        "unknown_ledger_fact",
                        f"ledger row {row_number} references unknown facts: {', '.join(unknown_refs)}",
                        relative_path,
                    )
                )
            if isinstance(event.get("source_fact_ids"), list):
                ledger_fact_refs[expected_type].update(
                    identifier
                    for identifier in event["source_fact_ids"]
                    if isinstance(identifier, str)
                )
            if expected_type == "thread":
                for key in ("event_id", "subject_id"):
                    identifier = event.get(key)
                    if isinstance(identifier, str):
                        thread_ids.add(identifier)
            if expected_type == "state" and event.get("subject_id") not in entity_ids:
                issues.append(
                    ValidationIssue(
                        "unknown_state_subject",
                        f"ledger row {row_number} references an unknown entity",
                        relative_path,
                    )
                )

    missing_state_links = sorted(state_change_ids - ledger_fact_refs["state"])
    if missing_state_links:
        issues.append(
            ValidationIssue(
                "missing_state_ledger_fact",
                f"state changes are absent from the state ledger: {', '.join(missing_state_links[:20])}",
                "ledgers/state_events.jsonl",
            )
        )
    missing_clue_links = sorted(clue_candidate_ids - ledger_fact_refs["clue"])
    if missing_clue_links:
        issues.append(
            ValidationIssue(
                "missing_clue_ledger_fact",
                f"clue candidates are absent from the clue ledger: {', '.join(missing_clue_links[:20])}",
                "ledgers/clue_events.jsonl",
            )
        )

    arc_path = book_dir / "arcs/arcs.jsonl"
    arcs = _read_jsonl(arc_path)
    arc_ids = {
        arc["arc_id"]
        for arc in arcs
        if isinstance(arc.get("arc_id"), str)
    }
    if len(arc_ids) != len(arcs):
        issues.append(
            ValidationIssue(
                "duplicate_arc_id",
                "arc IDs must be unique",
                "arcs/arcs.jsonl",
            )
        )
    seen_arc_payloads: set[str] = set()
    covered_chapters: set[int] = set()
    parent_graph: dict[str, list[str]] = {}
    for row_number, arc in enumerate(arcs, start=1):
        normalized_arc = dict(arc)
        normalized_arc.pop("arc_id", None)

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

        normalized_arc = compact(normalized_arc)
        arc_signature = json.dumps(normalized_arc, ensure_ascii=False, sort_keys=True)
        if arc_signature in seen_arc_payloads:
            issues.append(
                ValidationIssue(
                    "duplicate_arc_payload",
                    f"arc row {row_number} duplicates another arc with a different ID",
                    "arcs/arcs.jsonl",
                )
            )
        seen_arc_payloads.add(arc_signature)
        errors = schema_errors(arc, "arc_analysis.schema.json")
        if errors:
            issues.append(
                ValidationIssue(
                    "invalid_arc_schema",
                    f"arc row {row_number}: {'; '.join(errors[:3])}",
                    "arcs/arcs.jsonl",
                )
            )
        if arc.get("book_id") != book_id:
            issues.append(
                ValidationIssue(
                    "arc_book_mismatch",
                    f"arc row {row_number} belongs to another book",
                    "arcs/arcs.jsonl",
                )
            )
        start_chapter = arc.get("start_chapter")
        end_chapter = arc.get("end_chapter")
        if (
            isinstance(start_chapter, int)
            and isinstance(end_chapter, int)
            and start_chapter > end_chapter
        ):
            issues.append(
                ValidationIssue(
                    "invalid_arc_range",
                    f"arc {arc.get('arc_id')} starts after it ends",
                    "arcs/arcs.jsonl",
                )
            )
        if isinstance(start_chapter, int) and isinstance(end_chapter, int) and start_chapter <= end_chapter:
            covered_chapters.update(
                chapter_id
                for chapter_id in fact_chapters
                if start_chapter <= chapter_id <= end_chapter
            )
        if (
            fact_chapters
            and isinstance(start_chapter, int)
            and isinstance(end_chapter, int)
            and (
                start_chapter < min(fact_chapters)
                or end_chapter > max(fact_chapters)
            )
        ):
            issues.append(
                ValidationIssue(
                    "arc_outside_fact_range",
                    f"arc {arc.get('arc_id')} falls outside analyzed chapters",
                    "arcs/arcs.jsonl",
                )
            )
        unknown_parents = sorted(
            set(arc.get("parent_arc_ids", [])) - arc_ids
            if isinstance(arc.get("parent_arc_ids"), list)
            else set()
        )
        if unknown_parents:
            issues.append(
                ValidationIssue(
                    "unknown_parent_arc",
                    f"arc {arc.get('arc_id')} references unknown parents: {', '.join(unknown_parents)}",
                    "arcs/arcs.jsonl",
                )
            )
        if isinstance(arc.get("arc_id"), str):
            parent_graph[arc["arc_id"]] = [
                parent
                for parent in arc.get("parent_arc_ids", [])
                if isinstance(parent, str)
            ]
        unknown_threads = sorted(
            set(arc.get("thread_ids", [])) - thread_ids
            if isinstance(arc.get("thread_ids"), list)
            else set()
        )
        if unknown_threads:
            issues.append(
                ValidationIssue(
                    "unknown_arc_thread",
                    f"arc {arc.get('arc_id')} references unknown threads: {', '.join(unknown_threads)}",
                    "arcs/arcs.jsonl",
                )
            )
        for stage_number, stage in enumerate(arc.get("stages", []), start=1):
            if not isinstance(stage, dict):
                continue
            unknown_refs = sorted(set(stage.get("source_fact_ids", [])) - fact_ids)
            if unknown_refs:
                issues.append(
                    ValidationIssue(
                        "unknown_arc_fact",
                        f"arc {arc.get('arc_id')} stage {stage_number} references unknown facts: {', '.join(unknown_refs)}",
                        "arcs/arcs.jsonl",
                    )
                )
            chapter_range = stage.get("chapter_range")
            if (
                isinstance(chapter_range, list)
                and len(chapter_range) == 2
                and all(isinstance(value, int) for value in chapter_range)
                and (
                    chapter_range[0] > chapter_range[1]
                    or (
                        isinstance(start_chapter, int)
                        and isinstance(end_chapter, int)
                        and not (
                            start_chapter <= chapter_range[0]
                            and chapter_range[1] <= end_chapter
                        )
                    )
                )
            ):
                issues.append(
                    ValidationIssue(
                        "invalid_arc_stage_range",
                        f"arc {arc.get('arc_id')} stage {stage_number} falls outside its arc",
                        "arcs/arcs.jsonl",
                    )
                )
        for hypothesis_number, hypothesis in enumerate(
            arc.get("craft_hypotheses", []), start=1
        ):
            if not isinstance(hypothesis, dict):
                continue
            unknown_evidence = sorted(
                set(hypothesis.get("evidence", [])) - fact_ids
                if isinstance(hypothesis.get("evidence"), list)
                else set()
            )
            if unknown_evidence:
                issues.append(
                    ValidationIssue(
                        "unknown_craft_evidence",
                        f"arc {arc.get('arc_id')} hypothesis {hypothesis_number} references unknown facts: {', '.join(unknown_evidence)}",
                        "arcs/arcs.jsonl",
                    )
                )
            out_of_range_evidence = sorted(
                identifier
                for identifier in hypothesis.get("evidence", [])
                if isinstance(identifier, str)
                and identifier in fact_chapter_map
                and isinstance(start_chapter, int)
                and isinstance(end_chapter, int)
                and not (start_chapter <= fact_chapter_map[identifier] <= end_chapter)
            )
            if out_of_range_evidence:
                issues.append(
                    ValidationIssue(
                        "craft_evidence_outside_arc",
                        f"arc {arc.get('arc_id')} cites facts outside its range: {', '.join(out_of_range_evidence)}",
                        "arcs/arcs.jsonl",
                    )
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def has_parent_cycle(arc_id: str) -> bool:
        if arc_id in visiting:
            return True
        if arc_id in visited:
            return False
        visiting.add(arc_id)
        cyclic = any(has_parent_cycle(parent) for parent in parent_graph.get(arc_id, []))
        visiting.remove(arc_id)
        visited.add(arc_id)
        return cyclic

    if any(has_parent_cycle(arc_id) for arc_id in parent_graph):
        issues.append(
            ValidationIssue(
                "cyclic_arc_parents",
                "arc parent relationships contain a cycle",
                "arcs/arcs.jsonl",
            )
        )

    if arcs:
        uncovered = sorted(fact_chapters - covered_chapters)
        if uncovered:
            issues.append(
                ValidationIssue(
                    "uncovered_arc_chapters",
                    f"chapters are not covered by any arc: {', '.join(str(value) for value in uncovered[:20])}",
                    "arcs/arcs.jsonl",
                )
            )
    if require_complete:
        missing = sorted(fact_chapters - seen_annotation_chapters)
        if missing:
            preview = ", ".join(str(chapter_id) for chapter_id in missing[:20])
            issues.append(
                ValidationIssue(
                    "missing_annotation_chapters",
                    f"annotations are missing for chapters: {preview}",
                    "facts/chapter_annotations",
                )
            )
    return ValidationReport(not issues, tuple(issues))
