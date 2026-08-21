import json
import tempfile
import unittest
from pathlib import Path

from novel_analysis.workspace import (
    ground_fact_parts,
    ingest_book,
    initialize_book,
    create_analysis_run,
    workspace_status,
)
from novel_analysis.structure import validate_structure


class StructureValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.book_dir = initialize_book(self.root, "demo-book", "测试小说")
        source = self.root / "input.txt"
        source.write_text("第一章 一\n林玄推开石门。\n", encoding="utf-8")
        result = ingest_book(self.root, "demo-book", source)
        self.source_hash = result.source_sha256
        fact = {
            "schema_version": "1.0.0",
            "book_id": "demo-book",
            "chapter_id": 1,
            "source_sha256": self.source_hash,
            "extractor": {"agent": "novel-extractor", "prompt_version": "1.0.0", "run_id": "run-test", "model": None},
            "characters": [{"entity_id": "CHAR-00001", "name": "林玄"}],
            "events": [{
                "event_id": "C000001-E001",
                "summary": "林玄推开石门",
                "actors": [{"entity_id": "CHAR-00001", "name": "林玄"}],
                "evidence": [{"quote": "林玄推开石门。", "occurrence": 1}],
                "confidence": 0.99,
            }],
            "information_reveals": [],
            "state_changes": [],
            "clue_candidates": [],
            "unknowns": [],
        }
        facts = self.book_dir / "facts/chapter_facts/part-000001-000001.jsonl"
        facts.write_text(json.dumps(fact, ensure_ascii=False) + "\n", encoding="utf-8")
        ground_fact_parts(self.root, "demo-book")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_structure(self, event_id="C000001-E001"):
        annotation = {
            "schema_version": "1.0.0",
            "book_id": "demo-book",
            "chapter_id": 1,
            "protagonist_goal": "进入石门",
            "conflicts": [{
                "type": "information",
                "opponent": None,
                "stakes": "未知",
                "event_ids": [event_id],
            }],
            "emotional_curve": ["平静", "好奇"],
            "payoffs": [],
            "rewards": [],
            "hook": None,
            "confidence": 0.8,
        }
        entity = {
            "schema_version": "1.0.0",
            "book_id": "demo-book",
            "entity_id": "CHAR-00001",
            "entity_type": "character",
            "canonical_name": "林玄",
            "aliases": [],
            "first_chapter": 1,
            "status": "active",
        }
        ledger = {
            "schema_version": "1.0.0",
            "book_id": "demo-book",
            "ledger_type": "state",
            "event_id": "STATE-000001",
            "chapter_id": 1,
            "operation": "update",
            "subject_id": "CHAR-00001",
            "payload": {"location": "石门前"},
            "source_fact_ids": ["C000001-E001"],
            "confidence": 0.9,
        }
        (self.book_dir / "facts/chapter_annotations/part-000001-000001.jsonl").write_text(
            json.dumps(annotation, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (self.book_dir / "index/entities.jsonl").write_text(
            json.dumps(entity, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (self.book_dir / "ledgers/state_events.jsonl").write_text(
            json.dumps(ledger, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def test_accepts_a_complete_grounded_structure_layer(self):
        self._write_structure()

        report = validate_structure(self.root, "demo-book", require_complete=True)

        self.assertTrue(report.valid, report.issues)

    def test_rejects_annotation_references_to_unknown_facts(self):
        self._write_structure(event_id="C000001-E999")

        report = validate_structure(self.root, "demo-book", require_complete=True)

        self.assertFalse(report.valid)
        self.assertIn("unknown_annotation_fact", [issue.code for issue in report.issues])

    def test_status_reports_link_stage_from_annotation_partitions(self):
        run_dir = create_analysis_run(
            self.root, "demo-book", batch_size=1, run_id="run-test"
        )
        self._write_structure()

        status = workspace_status(self.root, "demo-book")

        stages = status["run_progress"][0]["stages"]
        self.assertEqual(stages["link"]["completed_jobs"], 1)
        self.assertEqual(stages["link"]["pending_jobs"], 0)
        self.assertEqual(stages["analyze"]["status"], "pending")
        self.assertTrue((run_dir / "jobs/link.jsonl").is_file())

    def test_rejects_fact_entity_ids_missing_from_the_registry(self):
        self._write_structure()
        (self.book_dir / "index/entities.jsonl").write_text("", encoding="utf-8")

        report = validate_structure(self.root, "demo-book", require_complete=True)

        self.assertFalse(report.valid)
        self.assertIn("unknown_fact_entity", [issue.code for issue in report.issues])

    def test_rejects_state_changes_missing_from_the_state_ledger(self):
        facts_path = self.book_dir / "facts/chapter_facts/part-000001-000001.jsonl"
        fact = json.loads(facts_path.read_text(encoding="utf-8"))
        fact["state_changes"] = [{
            "change_id": "C000001-S001",
            "subject": {"entity_id": "CHAR-00001", "name": "林玄"},
            "field": "location",
            "before": "门外",
            "after": "门前",
            "evidence": [{"quote": "林玄推开石门。", "occurrence": 1}],
            "confidence": 0.9,
        }]
        facts_path.write_text(
            json.dumps(fact, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        ground_fact_parts(self.root, "demo-book")
        self._write_structure()

        report = validate_structure(self.root, "demo-book", require_complete=True)

        self.assertFalse(report.valid)
        self.assertIn("missing_state_ledger_fact", [issue.code for issue in report.issues])

    def test_rejects_logically_reversed_arc_ranges(self):
        self._write_structure()
        arc = {
            "schema_version": "1.0.0",
            "book_id": "demo-book",
            "arc_id": "ARC-M-0001",
            "level": "medium",
            "start_chapter": 2,
            "end_chapter": 1,
            "problem": "进入石门",
            "stages": [{
                "name": "反向阶段",
                "chapter_range": [2, 1],
                "function": "测试非法区间",
                "source_fact_ids": ["C000001-E001"],
            }],
            "drivers": [],
            "payoffs": [],
            "new_questions": [],
            "craft_hypotheses": [{
                "claim": "石门推动目标",
                "evidence": ["C000001-E001"],
                "alternative_explanation": None,
                "confidence": 0.8,
            }],
        }
        (self.book_dir / "arcs/arcs.jsonl").write_text(
            json.dumps(arc, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        report = validate_structure(self.root, "demo-book", require_complete=True)

        self.assertFalse(report.valid)
        self.assertIn("invalid_arc_range", [issue.code for issue in report.issues])

    def test_structure_gate_propagates_invalid_fact_contracts(self):
        self._write_structure()
        facts_path = self.book_dir / "facts/chapter_facts/part-000001-000001.jsonl"
        fact = json.loads(facts_path.read_text(encoding="utf-8"))
        fact["forbidden"] = True
        facts_path.write_text(json.dumps(fact) + "\n", encoding="utf-8")

        report = validate_structure(self.root, "demo-book", require_complete=True)

        self.assertFalse(report.valid)
        self.assertIn("invalid_fact_schema", [issue.code for issue in report.issues])

    def test_rejects_fabricated_craft_hypothesis_evidence(self):
        self._write_structure()
        arc = {
            "schema_version": "1.0.0",
            "book_id": "demo-book",
            "arc_id": "ARC-M-0001",
            "level": "medium",
            "start_chapter": 1,
            "end_chapter": 1,
            "problem": "进入石门",
            "stages": [{
                "name": "进入",
                "chapter_range": [1, 1],
                "function": "推动目标",
                "source_fact_ids": ["C000001-E001"],
            }],
            "drivers": [],
            "payoffs": [],
            "new_questions": [],
            "craft_hypotheses": [{
                "claim": "伪造判断",
                "evidence": ["C000001-E999"],
                "alternative_explanation": None,
                "confidence": 1.0,
            }],
        }
        (self.book_dir / "arcs/arcs.jsonl").write_text(
            json.dumps(arc, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        report = validate_structure(self.root, "demo-book", require_complete=True)

        self.assertFalse(report.valid)
        self.assertIn("unknown_craft_evidence", [issue.code for issue in report.issues])

    def test_one_byte_book_dna_does_not_complete_distillation(self):
        create_analysis_run(self.root, "demo-book", batch_size=1, run_id="run-test")
        self._write_structure()
        arc = {
            "schema_version": "1.0.0",
            "book_id": "demo-book",
            "arc_id": "ARC-M-0001",
            "level": "medium",
            "start_chapter": 1,
            "end_chapter": 1,
            "problem": "进入石门",
            "stages": [{
                "name": "进入",
                "chapter_range": [1, 1],
                "function": "推动目标",
                "source_fact_ids": ["C000001-E001"],
            }],
            "drivers": [],
            "payoffs": [],
            "new_questions": [],
            "craft_hypotheses": [{
                "claim": "石门推动目标",
                "evidence": ["C000001-E001"],
                "alternative_explanation": None,
                "confidence": 0.8,
            }],
        }
        (self.book_dir / "arcs/arcs.jsonl").write_text(
            json.dumps(arc, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        headings = {
            "characters.md": "# Characters",
            "pacing.md": "# Pacing",
            "hooks.md": "# Hooks",
            "payoffs.md": "# Payoffs",
        }
        for filename, heading in headings.items():
            (self.book_dir / "analysis" / filename).write_text(
                heading + "\nC000001-E001\n" + "有效分析。" * 120, encoding="utf-8"
            )
        (self.book_dir / "distilled/book_dna.md").write_text("x", encoding="utf-8")

        status = workspace_status(self.root, "demo-book")

        stages = status["run_progress"][0]["stages"]
        self.assertEqual(stages["analyze"]["status"], "completed")
        self.assertEqual(stages["distill"]["status"], "pending")

    def test_rejects_duplicate_arc_payloads_with_different_ids(self):
        self._write_structure()
        base = {
            "schema_version": "1.0.0",
            "book_id": "demo-book",
            "level": "medium",
            "start_chapter": 1,
            "end_chapter": 1,
            "problem": "进入石门",
            "stages": [{
                "name": "进入",
                "chapter_range": [1, 1],
                "function": "推动目标",
                "source_fact_ids": ["C000001-E001"],
            }],
            "drivers": [],
            "payoffs": [],
            "new_questions": [],
            "craft_hypotheses": [{
                "claim": "石门推动目标",
                "evidence": ["C000001-E001"],
                "alternative_explanation": None,
                "confidence": 0.8,
            }],
        }
        arcs = [dict(base, arc_id="ARC-M-0001"), dict(base, arc_id="ARC-M-0002")]
        (self.book_dir / "arcs/arcs.jsonl").write_text(
            "".join(json.dumps(arc, ensure_ascii=False) + "\n" for arc in arcs),
            encoding="utf-8",
        )

        report = validate_structure(self.root, "demo-book", require_complete=True)

        self.assertFalse(report.valid)
        self.assertIn("duplicate_arc_payload", [issue.code for issue in report.issues])


if __name__ == "__main__":
    unittest.main()
