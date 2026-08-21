import json
import tempfile
import unittest
from pathlib import Path

from novel_analysis.library import validate_library
from novel_analysis.library import register_distilled_book
from novel_analysis.completion import finalize_book
from novel_analysis.completion import validate_completion_manifest
from novel_analysis.workspace import (
    WorkspaceError,
    create_analysis_run,
    ground_fact_parts,
    ingest_book,
    initialize_book,
    materialize_run_inputs,
    materialize_review_inputs,
)


class LibraryValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "library").mkdir()
        (self.root / "library/books.jsonl").touch()
        for book_id in ("book-a", "book-b"):
            self._create_finalized_book(book_id)
            register_distilled_book(self.root, book_id)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_finalized_book(self, book_id):
        book_dir = initialize_book(self.root, book_id, book_id)
        source = self.root / f"{book_id}.txt"
        source.write_text(f"第一章 一\n{book_id}。主角推开石门。\n", encoding="utf-8")
        result = ingest_book(self.root, book_id, source)
        create_analysis_run(self.root, book_id, batch_size=1, run_id="run-test")
        materialize_run_inputs(self.root, book_id, "run-test")
        fact = {
            "schema_version": "1.0.0",
            "book_id": book_id,
            "chapter_id": 1,
            "source_sha256": result.source_sha256,
            "extractor": {"agent": "novel-extractor", "prompt_version": "1.0.0", "run_id": "run-test", "model": None},
            "events": [{
                "event_id": "C000001-E001",
                "summary": "主角推开石门",
                "evidence": [{"quote": "主角推开石门。", "occurrence": 1}],
                "confidence": 0.9,
            }],
            "information_reveals": [],
            "state_changes": [],
            "clue_candidates": [],
            "unknowns": [],
        }
        (book_dir / "facts/chapter_facts/part-000001-000001.jsonl").write_text(
            json.dumps(fact, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        ground_fact_parts(self.root, book_id)
        materialize_review_inputs(self.root, book_id, "run-test")
        annotation = {
            "schema_version": "1.0.0",
            "book_id": book_id,
            "chapter_id": 1,
            "protagonist_goal": "进入石门",
            "conflicts": [],
            "emotional_curve": [],
            "payoffs": [],
            "rewards": [],
            "hook": None,
            "confidence": 0.9,
        }
        (book_dir / "facts/chapter_annotations/part-000001-000001.jsonl").write_text(
            json.dumps(annotation, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        arc = {
            "schema_version": "1.0.0",
            "book_id": book_id,
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
        (book_dir / "arcs/arcs.jsonl").write_text(
            json.dumps(arc, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        headings = {
            "characters.md": "# Characters",
            "pacing.md": "# Pacing",
            "hooks.md": "# Hooks",
            "payoffs.md": "# Payoffs",
        }
        for filename, heading in headings.items():
            (book_dir / "analysis" / filename).write_text(
                heading + "\nC000001-E001\n" + "有效分析。" * 120, encoding="utf-8"
            )
        dna = (
            "# Book DNA\n## Evidence scope\nARC-M-0001\n## Reading drivers\n机制。\n## Narrative loops\n循环。\n"
            "## Payoff and reward system\n回报。\n## Character behavior rules\n行为。\n"
            "## Pacing model\n节奏。\n## Hooks and information control\n钩子。\n"
            "## Foreshadowing model\n伏笔。\n## Craft hypotheses\n假设。\n"
            "## Transfer boundary\n边界。\n" + "有效内容。" * 350
        )
        (book_dir / "distilled/book_dna.md").write_text(dna, encoding="utf-8")
        finalize_book(self.root, book_id)

    def _pattern(self, source_books):
        return {
            "schema_version": "1.0.0",
            "pattern_id": "PAT-00001",
            "name": "身份反转",
            "mechanism": "公开验证纠正群体误判",
            "applicable_contexts": ["公开竞争"],
            "source_books": source_books,
            "variations": [],
            "failure_modes": ["重复使用"],
            "originality_constraints": ["更换资源和关系结构"],
            "confidence": 0.8,
        }

    def test_rejects_two_evidence_entries_from_the_same_book(self):
        pattern = self._pattern([
            {"book_id": "book-a", "evidence_refs": ["arcs/a.md"]},
            {"book_id": "book-a", "evidence_refs": ["analysis/hooks.md"]},
        ])
        (self.root / "library/patterns.jsonl").write_text(
            json.dumps(pattern, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        report = validate_library(self.root)

        self.assertFalse(report.valid)
        self.assertIn("duplicate_source_book", [issue.code for issue in report.issues])

    def test_accepts_a_pattern_supported_by_distinct_books(self):
        pattern = self._pattern([
            {"book_id": "book-a", "evidence_refs": ["arcs/arcs.jsonl"]},
            {"book_id": "book-b", "evidence_refs": ["arcs/arcs.jsonl"]},
        ])
        (self.root / "library/patterns.jsonl").write_text(
            json.dumps(pattern, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        report = validate_library(self.root)

        self.assertTrue(report.valid, report.issues)

    def test_rejects_aliases_of_the_same_physical_book(self):
        pattern = self._pattern([
            {"book_id": "book-a", "evidence_refs": ["arcs/arcs.jsonl"]},
            {"book_id": "book-a/.", "evidence_refs": ["arcs/arcs.jsonl"]},
        ])
        (self.root / "library/patterns.jsonl").write_text(
            json.dumps(pattern, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        report = validate_library(self.root)

        self.assertFalse(report.valid)

    def test_rejects_a_symlinked_library_root(self):
        outside_temp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temp.cleanup)
        for path in (self.root / "library").iterdir():
            path.unlink()
        (self.root / "library").rmdir()
        (self.root / "library").symlink_to(
            Path(outside_temp.name), target_is_directory=True
        )

        report = validate_library(self.root)

        self.assertFalse(report.valid)
        self.assertIn("invalid_library", [issue.code for issue in report.issues])

    def test_completion_manifest_cannot_omit_hashed_artifacts(self):
        completion_path = self.root / "books/book-a/distilled/completion.json"
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        completion["artifacts"] = {}
        completion_path.write_text(json.dumps(completion) + "\n", encoding="utf-8")

        report = validate_completion_manifest(self.root, "book-a")

        self.assertFalse(report.valid)
        self.assertIn("completion_artifact_set_mismatch", [issue.code for issue in report.issues])

    def test_completion_detects_post_finalization_fact_changes(self):
        facts_path = self.root / "books/book-a/facts/chapter_facts/part-000001-000001.jsonl"
        facts_path.write_text(facts_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        report = validate_completion_manifest(self.root, "book-a")

        self.assertFalse(report.valid)
        self.assertIn("completion_artifact_mismatch", [issue.code for issue in report.issues])

    def test_registration_can_refresh_after_refinalization(self):
        pattern = self._pattern([
            {"book_id": "book-a", "evidence_refs": ["arcs/arcs.jsonl"]},
            {"book_id": "book-b", "evidence_refs": ["arcs/arcs.jsonl"]},
        ])
        (self.root / "library/patterns.jsonl").write_text(
            json.dumps(pattern, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        report_path = self.root / "books/book-a/analysis/characters.md"
        report_path.write_text(
            report_path.read_text(encoding="utf-8") + "\n补充分析。",
            encoding="utf-8",
        )
        finalize_book(self.root, "book-a")

        stale_report = validate_library(self.root)

        self.assertFalse(stale_report.valid)
        self.assertIn(
            "registration_hash_mismatch",
            [issue.code for issue in stale_report.issues],
        )

        register_distilled_book(self.root, "book-a")

        report = validate_library(self.root)
        registrations = [
            json.loads(line)
            for line in (self.root / "library/books.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]

        self.assertTrue(report.valid, report.issues)
        self.assertEqual(len(registrations), 2)

    def test_completion_rejects_noncanonical_extract_job_ids(self):
        jobs_path = self.root / "books/book-a/runs/run-test/jobs/extract.jsonl"
        job = json.loads(jobs_path.read_text(encoding="utf-8"))
        job["job_id"] = "foo"
        jobs_path.write_text(json.dumps(job) + "\n", encoding="utf-8")

        report = validate_completion_manifest(self.root, "book-a")

        self.assertFalse(report.valid)
        self.assertIn(
            "noncanonical_extract_job_id",
            [issue.code for issue in report.issues],
        )

    def test_registration_refresh_removes_duplicate_rows(self):
        registry_path = self.root / "library/books.jsonl"
        registrations = registry_path.read_text(encoding="utf-8").splitlines()
        registry_path.write_text(
            "\n".join([*registrations, registrations[0]]) + "\n",
            encoding="utf-8",
        )

        register_distilled_book(self.root, "book-a")

        refreshed = [
            json.loads(line)
            for line in registry_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [row["book_id"] for row in refreshed].count("book-a"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
