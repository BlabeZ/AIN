import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from novel_analysis.workspace import (
    WorkspaceError,
    chapter_batch,
    create_analysis_run,
    decode_source,
    ground_fact_parts,
    ingest_book,
    initialize_book,
    materialize_run_inputs,
    materialize_review_inputs,
    split_chapters,
    validate_book,
    validate_fact_parts,
    workspace_status,
)


class SplitChaptersTests(unittest.TestCase):
    def test_preserves_front_matter_titles_and_source_offsets(self):
        text = "简介内容\n\n第一章 初来乍到\n正文甲。\n第2章：危机\n正文乙。\n"

        chapters = split_chapters(text)

        self.assertEqual([chapter.chapter_id for chapter in chapters], [0, 1, 2])
        self.assertEqual([chapter.title for chapter in chapters], ["卷首内容", "初来乍到", "危机"])
        self.assertEqual(chapters[0].kind, "front_matter")
        self.assertEqual(chapters[1].kind, "chapter")
        for chapter in chapters:
            self.assertEqual(text[chapter.char_start : chapter.char_end], chapter.raw_text)
            self.assertEqual(
                text[chapter.content_start : chapter.content_end], chapter.content
            )

    def test_treats_a_source_without_headings_as_one_chapter(self):
        chapters = split_chapters("只有正文，没有标准章节标题。")

        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0].chapter_id, 1)
        self.assertEqual(chapters[0].title, "正文")
        self.assertEqual(chapters[0].content, "只有正文，没有标准章节标题。")

    def test_strips_middle_dot_and_full_width_space_from_chapter_titles(self):
        chapters = split_chapters("第一章　开端\n甲\n第二章·风起\n乙\n")

        self.assertEqual([chapter.title for chapter in chapters], ["开端", "风起"])


class SourceDecodingTests(unittest.TestCase):
    def test_decodes_gb18030_source(self):
        expected = "第一章 开始\n这是正文。"

        decoded, encoding = decode_source(expected.encode("gb18030"))

        self.assertEqual(decoded, expected)
        self.assertEqual(encoding, "gb18030")


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rejects_book_ids_that_can_escape_the_workspace(self):
        with self.assertRaises(WorkspaceError):
            initialize_book(self.root, "../outside", "越界测试")

        self.assertFalse((self.root.parent / "outside").exists())

    def test_rejects_a_book_directory_symlink_that_escapes_the_workspace(self):
        outside_temp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temp.cleanup)
        outside = Path(outside_temp.name)
        (self.root / "books").mkdir()
        (self.root / "books/demo-book").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(WorkspaceError):
            initialize_book(self.root, "demo-book", "越界测试")

        self.assertFalse((outside / "book.json").exists())

    def test_rejects_a_book_directory_symlink_even_when_it_points_inside_workspace(self):
        target = self.root / "internal-target"
        target.mkdir()
        (self.root / "books").mkdir()
        (self.root / "books/demo-book").symlink_to(target, target_is_directory=True)

        with self.assertRaises(WorkspaceError):
            initialize_book(self.root, "demo-book", "越界测试")

    def test_rejects_a_child_symlink_during_book_initialization(self):
        outside_temp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temp.cleanup)
        book_dir = self.root / "books/demo-book"
        book_dir.mkdir(parents=True)
        (book_dir / "index").symlink_to(
            Path(outside_temp.name), target_is_directory=True
        )

        with self.assertRaises(WorkspaceError):
            initialize_book(self.root, "demo-book", "越界测试")

        self.assertFalse((Path(outside_temp.name) / "entities.jsonl").exists())

    def test_initializes_a_multi_stage_book_workspace(self):
        book_dir = initialize_book(self.root, "demo-book", "测试小说", author="某作者")

        manifest = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["book_id"], "demo-book")
        self.assertEqual(manifest["title"], "测试小说")
        self.assertEqual(manifest["author"], "某作者")
        for relative_path in (
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
        ):
            self.assertTrue((book_dir / relative_path).is_dir(), relative_path)

    def test_ingests_source_and_writes_a_reproducible_chapter_index(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_bytes(
            "第一章 开始\r\n正文一。\r\n第二章 继续\r\n正文二。\r\n".encode("gb18030")
        )

        result = ingest_book(self.root, "demo-book", source_path)

        normalized = "第一章 开始\n正文一。\n第二章 继续\n正文二。\n"
        book_dir = self.root / "books" / "demo-book"
        self.assertEqual(
            (book_dir / "source/original.txt").read_text(encoding="utf-8"), normalized
        )
        index_rows = [
            json.loads(line)
            for line in (book_dir / "index/chapters.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(result.chapter_count, 2)
        self.assertEqual([row["title"] for row in index_rows], ["开始", "继续"])
        self.assertEqual(
            result.source_sha256,
            hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )

    def test_rejects_reingestion_that_would_stale_downstream_artifacts(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n原正文。\n", encoding="utf-8")
        ingest_book(self.root, "demo-book", source_path)
        source_path.write_text("第一章 一\n新正文。\n", encoding="utf-8")

        with self.assertRaises(WorkspaceError):
            ingest_book(self.root, "demo-book", source_path)

        stored = self.root / "books/demo-book/source/original.txt"
        self.assertIn("原正文", stored.read_text(encoding="utf-8"))

    def test_rejects_a_symlink_inside_a_book_before_ingestion(self):
        book_dir = initialize_book(self.root, "demo-book", "测试小说")
        outside_temp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temp.cleanup)
        (book_dir / "source").rmdir()
        (book_dir / "source").symlink_to(Path(outside_temp.name), target_is_directory=True)
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n正文。\n", encoding="utf-8")

        with self.assertRaises(WorkspaceError):
            ingest_book(self.root, "demo-book", source_path)

        self.assertFalse((Path(outside_temp.name) / "original.txt").exists())

    def test_creates_deterministic_extraction_jobs(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text(
            "第一章 一\n甲\n第二章 二\n乙\n第三章 三\n丙\n第四章 四\n丁\n第五章 五\n戊\n",
            encoding="utf-8",
        )
        ingest_book(self.root, "demo-book", source_path)

        run_dir = create_analysis_run(
            self.root, "demo-book", batch_size=2, run_id="run-test"
        )

        jobs = [
            json.loads(line)
            for line in (run_dir / "jobs/extract.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(
            [(job["start_chapter"], job["end_chapter"]) for job in jobs],
            [(1, 2), (3, 4), (5, 5)],
        )
        self.assertEqual(jobs[0]["output_path"], "facts/chapter_facts/part-000001-000002.jsonl")
        self.assertEqual(
            jobs[0]["input_path"],
            "runs/run-test/inputs/extract-000001-000002.json",
        )
        link_jobs = [
            json.loads(line)
            for line in (run_dir / "jobs/link.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(link_jobs[0]["input_path"], jobs[0]["output_path"])
        self.assertEqual(
            link_jobs[0]["output_path"],
            "facts/chapter_annotations/part-000001-000002.jsonl",
        )
        self.assertTrue((run_dir / "jobs/analyze.jsonl").is_file())
        self.assertTrue((run_dir / "jobs/distill.jsonl").is_file())

    def test_materializes_all_extraction_inputs_without_repeated_source_reads(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text(
            "第一章 一\n甲\n第二章 二\n乙\n第三章 三\n丙\n",
            encoding="utf-8",
        )
        ingest_book(self.root, "demo-book", source_path)
        run_dir = create_analysis_run(
            self.root, "demo-book", batch_size=2, run_id="run-test"
        )

        count = materialize_run_inputs(self.root, "demo-book", "run-test")

        inputs = sorted((run_dir / "inputs").glob("*.json"))
        self.assertEqual(count, 2)
        self.assertEqual(len(inputs), 2)
        first = json.loads(inputs[0].read_text(encoding="utf-8"))
        self.assertEqual([row["chapter_id"] for row in first["chapters"]], [1, 2])

    def test_materialized_chapter_text_is_split_into_readable_json_lines(self):
        initialize_book(self.root, "demo-book", "测试小说")
        chapter_text = "甲\n" * 1_500
        source_path = self.root / "input.txt"
        source_path.write_text(f"第一章 一\n{chapter_text}", encoding="utf-8")
        ingest_book(self.root, "demo-book", source_path)
        run_dir = create_analysis_run(
            self.root, "demo-book", batch_size=1, run_id="run-test"
        )

        materialize_run_inputs(self.root, "demo-book", "run-test")

        payload = json.loads(
            (run_dir / "inputs/extract-000001-000001.json").read_text(
                encoding="utf-8"
            )
        )
        chapter = payload["chapters"][0]
        indexed_source = (
            self.root / "books/demo-book/source/original.txt"
        ).read_text(encoding="utf-8")
        self.assertNotIn("raw_text", chapter)
        self.assertEqual(
            "".join(chapter["raw_text_chunks"]),
            indexed_source[chapter["char_start"] : chapter["char_end"]],
        )
        self.assertGreater(len(chapter["raw_text_chunks"]), 1)
        self.assertTrue(
            all(
                len(json.dumps(chunk, ensure_ascii=False)) < 2_000
                for chunk in chapter["raw_text_chunks"]
            )
        )

    def test_materialization_rejects_a_source_modified_after_ingestion(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n原文。\n", encoding="utf-8")
        ingest_book(self.root, "demo-book", source_path)
        create_analysis_run(self.root, "demo-book", batch_size=1, run_id="run-test")
        (self.root / "books/demo-book/source/original.txt").write_text(
            "第一章 一\n篡改。\n", encoding="utf-8"
        )

        with self.assertRaises(WorkspaceError):
            materialize_run_inputs(self.root, "demo-book", "run-test")

    def test_planner_uses_the_same_batch_limit_as_batch_reader(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n正文。\n", encoding="utf-8")
        ingest_book(self.root, "demo-book", source_path)

        with self.assertRaises(WorkspaceError):
            create_analysis_run(self.root, "demo-book", batch_size=51)

    def test_recovers_an_incomplete_run_before_replanning(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n甲\n", encoding="utf-8")
        ingest_book(self.root, "demo-book", source_path)
        first_run = create_analysis_run(
            self.root, "demo-book", batch_size=1, run_id="run-broken"
        )
        (first_run / "manifest.json").unlink()

        second_run = create_analysis_run(
            self.root, "demo-book", batch_size=1, run_id="run-recovered"
        )

        recovery = self.root / "books/demo-book/runs/_recovery"
        self.assertTrue(second_run.is_dir())
        self.assertEqual([path.name for path in recovery.iterdir()], ["run-broken"])

    def test_validation_detects_source_changes_after_ingestion(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n正文。\n", encoding="utf-8")
        ingest_book(self.root, "demo-book", source_path)
        original_path = self.root / "books/demo-book/source/original.txt"
        original_path.write_text("第一章 一\n正文被修改。\n", encoding="utf-8")

        report = validate_book(self.root, "demo-book")

        self.assertFalse(report.valid)
        self.assertIn("source_hash_mismatch", [issue.code for issue in report.issues])

    def test_fact_validation_requires_grounded_evidence_inside_the_chapter(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n主角推开石门。\n", encoding="utf-8")
        ingest_result = ingest_book(self.root, "demo-book", source_path)
        book_dir = self.root / "books/demo-book"
        index_row = json.loads(
            (book_dir / "index/chapters.jsonl").read_text(encoding="utf-8").strip()
        )
        fact_row = {
            "schema_version": "1.0.0",
            "book_id": "demo-book",
            "chapter_id": 1,
            "source_sha256": ingest_result.source_sha256,
            "extractor": {"agent": "novel-extractor", "prompt_version": "1.0.0", "run_id": None, "model": None},
            "events": [
                {
                    "event_id": "C000001-E001",
                    "summary": "主角推开石门",
                    "evidence": [
                        {
                            "start": index_row["content_start"],
                            "end": index_row["content_end"] - 1,
                            "quote": "主角推开石门。",
                            "occurrence": 1,
                        }
                    ],
                    "confidence": 0.99,
                }
            ],
            "information_reveals": [],
            "state_changes": [],
            "clue_candidates": [],
            "unknowns": [],
        }
        facts_path = book_dir / "facts/chapter_facts/part-000001-000001.jsonl"
        facts_path.write_text(
            json.dumps(fact_row, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        valid_report = validate_fact_parts(self.root, "demo-book")
        self.assertTrue(valid_report.valid, valid_report.issues)

        fact_row["events"][0]["evidence"][0]["end"] = index_row["char_end"] + 100
        facts_path.write_text(
            json.dumps(fact_row, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        invalid_report = validate_fact_parts(self.root, "demo-book")

        self.assertFalse(invalid_report.valid)
        self.assertIn("evidence_outside_chapter", [issue.code for issue in invalid_report.issues])

    def test_fact_validation_enforces_the_json_schema(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n主角推开石门。\n", encoding="utf-8")
        result = ingest_book(self.root, "demo-book", source_path)
        index = json.loads(
            (self.root / "books/demo-book/index/chapters.jsonl")
            .read_text(encoding="utf-8")
            .strip()
        )
        invalid_row = {
            "schema_version": "1.0.0",
            "book_id": "demo-book",
            "chapter_id": 1,
            "source_sha256": result.source_sha256,
            "extractor": {"agent": "novel-extractor", "prompt_version": "1.0.0", "run_id": None, "model": None},
            "events": [{
                "event_id": "BAD-ID",
                "summary": "主角推开石门",
                "evidence": [{
                    "quote": "主角推开石门。",
                    "occurrence": 1,
                    "start": index["content_start"],
                    "end": index["content_end"] - 1,
                }],
            }],
            "information_reveals": [],
            "state_changes": [],
            "clue_candidates": [],
            "unknowns": [],
        }
        path = self.root / "books/demo-book/facts/chapter_facts/part-000001-000001.jsonl"
        path.write_text(json.dumps(invalid_row) + "\n", encoding="utf-8")

        report = validate_fact_parts(self.root, "demo-book")

        self.assertFalse(report.valid)
        self.assertIn("invalid_fact_schema", [issue.code for issue in report.issues])

    def test_fact_validation_rejects_duplicate_ids_across_chapters(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n甲。\n第二章 二\n乙。\n", encoding="utf-8")
        result = ingest_book(self.root, "demo-book", source_path)
        index = [
            json.loads(line)
            for line in (self.root / "books/demo-book/index/chapters.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        rows = []
        for chapter in index:
            quote = "甲。" if chapter["chapter_id"] == 1 else "乙。"
            rows.append({
                "schema_version": "1.0.0",
                "book_id": "demo-book",
                "chapter_id": chapter["chapter_id"],
                "source_sha256": result.source_sha256,
                "extractor": {"agent": "novel-extractor", "prompt_version": "1.0.0", "run_id": None, "model": None},
                "events": [{
                    "event_id": "C000001-E001",
                    "summary": quote,
                    "evidence": [{
                        "quote": quote,
                        "occurrence": 1,
                        "start": chapter["content_start"],
                        "end": chapter["content_start"] + len(quote),
                    }],
                    "confidence": 0.9,
                }],
                "information_reveals": [],
                "state_changes": [],
                "clue_candidates": [],
                "unknowns": [],
            })
        facts = self.root / "books/demo-book/facts/chapter_facts/part-000001-000002.jsonl"
        facts.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

        report = validate_fact_parts(self.root, "demo-book", require_complete=True)

        self.assertFalse(report.valid)
        codes = [issue.code for issue in report.issues]
        self.assertIn("duplicate_fact_id", codes)
        self.assertIn("fact_id_chapter_mismatch", codes)

    def test_fact_validation_reports_json_scalars_instead_of_crashing(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n正文。\n", encoding="utf-8")
        ingest_book(self.root, "demo-book", source_path)
        facts = self.root / "books/demo-book/facts/chapter_facts/part-000001-000001.jsonl"
        facts.write_text("42\n", encoding="utf-8")

        report = validate_fact_parts(self.root, "demo-book")

        self.assertFalse(report.valid)
        self.assertIn("invalid_fact_jsonl", [issue.code for issue in report.issues])

    def test_grounding_converts_evidence_quotes_to_global_source_offsets(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text(
            "第一章 一\n石门关闭。主角推开石门。石门再次关闭。\n", encoding="utf-8"
        )
        ingest_result = ingest_book(self.root, "demo-book", source_path)
        create_analysis_run(self.root, "demo-book", batch_size=1, run_id="run-test")
        materialize_run_inputs(self.root, "demo-book", "run-test")
        book_dir = self.root / "books/demo-book"
        fact_row = {
            "schema_version": "1.0.0",
            "book_id": "demo-book",
            "chapter_id": 1,
            "source_sha256": ingest_result.source_sha256,
            "extractor": {"agent": "novel-extractor", "prompt_version": "1.0.0", "run_id": None, "model": None},
            "events": [
                {
                    "event_id": "C000001-E001",
                    "summary": "主角推开石门",
                    "evidence": [
                        {"quote": "主角推开石门。", "occurrence": 1}
                    ],
                    "confidence": 0.99,
                }
            ],
            "information_reveals": [],
            "state_changes": [],
            "clue_candidates": [],
            "unknowns": [],
        }
        facts_path = book_dir / "facts/chapter_facts/part-000001-000001.jsonl"
        facts_path.write_text(
            json.dumps(fact_row, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        grounded_count = ground_fact_parts(self.root, "demo-book")

        grounded = json.loads(facts_path.read_text(encoding="utf-8"))
        evidence = grounded["events"][0]["evidence"][0]
        source = (book_dir / "source/original.txt").read_text(encoding="utf-8")
        self.assertEqual(grounded_count, 1)
        self.assertEqual(source[evidence["start"] : evidence["end"]], evidence["quote"])
        self.assertTrue(validate_fact_parts(self.root, "demo-book").valid)

        count = materialize_review_inputs(
            self.root,
            "demo-book",
            "run-test",
            "part-000001-000001.jsonl",
        )
        review = json.loads(
            (
                book_dir
                / "runs/run-test/inputs/review-000001-000001.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(count, 1)
        self.assertEqual(review["chapters"][0]["facts"], grounded)
        self.assertEqual(
            "".join(review["chapters"][0]["source"]["raw_text_chunks"]),
            source,
        )

    def test_complete_fact_validation_reports_unprocessed_chapters(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n甲\n第二章 二\n乙\n", encoding="utf-8")
        ingest_book(self.root, "demo-book", source_path)

        report = validate_fact_parts(self.root, "demo-book", require_complete=True)

        self.assertFalse(report.valid)
        self.assertIn("missing_fact_chapters", [issue.code for issue in report.issues])

    def test_status_derives_run_progress_from_partition_outputs(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text(
            "第一章 一\n甲\n第二章 二\n乙\n第三章 三\n丙\n", encoding="utf-8"
        )
        ingest_book(self.root, "demo-book", source_path)
        create_analysis_run(self.root, "demo-book", batch_size=2, run_id="run-test")
        output = self.root / "books/demo-book/facts/chapter_facts/part-000001-000002.jsonl"
        output.write_text("{}\n", encoding="utf-8")

        status = workspace_status(self.root, "demo-book")

        self.assertEqual(status["run_progress"][0]["job_count"], 2)
        self.assertEqual(status["run_progress"][0]["completed_jobs"], 0)
        self.assertEqual(status["run_progress"][0]["invalid_jobs"], 1)
        self.assertEqual(status["run_progress"][0]["pending_jobs"], 1)

    def test_status_rejects_duplicate_fact_rows_for_a_job(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n甲\n第二章 二\n乙\n", encoding="utf-8")
        result = ingest_book(self.root, "demo-book", source_path)
        create_analysis_run(self.root, "demo-book", batch_size=2, run_id="run-test")
        row = {
            "schema_version": "1.0.0", "book_id": "demo-book",
            "source_sha256": result.source_sha256,
            "extractor": {"agent": "novel-extractor", "prompt_version": "1.0.0", "run_id": "run-test", "model": None},
            "events": [],
            "information_reveals": [], "state_changes": [],
            "clue_candidates": [], "unknowns": [],
        }
        rows = [dict(row, chapter_id=1), dict(row, chapter_id=1), dict(row, chapter_id=2)]
        output = self.root / "books/demo-book/facts/chapter_facts/part-000001-000002.jsonl"
        output.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")

        status = workspace_status(self.root, "demo-book")

        self.assertEqual(status["run_progress"][0]["completed_jobs"], 0)
        self.assertEqual(status["run_progress"][0]["invalid_jobs"], 1)

    def test_read_operations_reject_symlinks_inside_a_book(self):
        book_dir = initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text("第一章 一\n正文。\n", encoding="utf-8")
        ingest_book(self.root, "demo-book", source_path)
        outside_temp = tempfile.TemporaryDirectory()
        self.addCleanup(outside_temp.cleanup)
        outside = Path(outside_temp.name) / "source.txt"
        original = book_dir / "source/original.txt"
        outside.write_text(original.read_text(encoding="utf-8"), encoding="utf-8")
        original.unlink()
        original.symlink_to(outside)

        with self.assertRaises(WorkspaceError):
            validate_book(self.root, "demo-book")
        with self.assertRaises(WorkspaceError):
            workspace_status(self.root, "demo-book")
        with self.assertRaises(WorkspaceError):
            chapter_batch(self.root, "demo-book", 1, 1)

    def test_chapter_batch_rejects_unbounded_ranges(self):
        initialize_book(self.root, "demo-book", "测试小说")
        source_path = self.root / "input.txt"
        source_path.write_text(
            "".join(f"第{number}章 章{number}\n正文。\n" for number in range(1, 52)),
            encoding="utf-8",
        )
        ingest_book(self.root, "demo-book", source_path)

        with self.assertRaises(WorkspaceError):
            chapter_batch(self.root, "demo-book", 1, 51)


if __name__ == "__main__":
    unittest.main()
