import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.compare import promote_suite
from evals.core.datasets import load_jsonl, resolve_relevant_chunks
from evals.core.metrics import (
    list_f1,
    list_recall,
    max_spread,
    mean,
    mean_reciprocal_rank,
    median,
    recall_at_k_average,
    score_mae,
    valid_rate,
    within_tolerance_rate,
)
from evals.core.reporting import compare_reports, promotion_blockers
from evals.run_interview_scoring import score as score_interview
from evals.run_job_match import score as score_job_match

EVALS_DIR = Path(__file__).resolve().parents[1] / "evals" / "datasets"


class MetricTests(unittest.TestCase):
    def test_list_recall_ignores_case_and_punctuation(self) -> None:
        self.assertEqual(list_recall(["PyTorch", "python "], ["Python"]), 1.0)
        self.assertEqual(list_recall(["PyTorch"], ["Python"]), 0.0)
        self.assertEqual(list_recall([], []), 1.0)

    def test_list_f1_penalises_hallucinated_items(self) -> None:
        self.assertLess(list_f1(["python", "kubernetes"], ["python"]), 1.0)
        self.assertEqual(list_f1(["python"], ["python"]), 1.0)

    def test_score_error_metrics(self) -> None:
        pairs = [(70, 60), (50, 60)]
        self.assertEqual(score_mae(pairs), 10.0)
        self.assertEqual(within_tolerance_rate(pairs, 10), 1.0)
        self.assertEqual(within_tolerance_rate([(65, 60), (50, 60)], 5), 0.5)
        self.assertEqual(within_tolerance_rate(pairs, 5), 0.0)

    def test_retrieval_metrics(self) -> None:
        rankings = [["c02", "c01"], ["c08", "c01"]]
        relevant = [{"c01"}, {"c01", "c08"}]
        self.assertEqual(recall_at_k_average(rankings, relevant, 1), 0.25)
        self.assertEqual(recall_at_k_average(rankings, relevant, 2), 1.0)
        self.assertEqual(mean_reciprocal_rank(rankings, relevant), 0.75)

    def test_stability_helpers(self) -> None:
        self.assertEqual(median([3, 1, 2]), 2.0)
        self.assertEqual(median([1, 2, 3, 4]), 2.5)
        self.assertEqual(max_spread([70, 74, 80]), 10.0)
        self.assertEqual(max_spread([70]), 0.0)
        self.assertEqual(mean([]), 0.0)

    def test_valid_rate_counts_errors(self) -> None:
        records = [{"error": None}, {"error": "RuntimeError"}]
        self.assertEqual(valid_rate(records), 0.5)

    def test_compare_reports_computes_delta(self) -> None:
        baseline = {"metrics": {"mrr": 0.8, "samples": 5}}
        candidate = {"metrics": {"mrr": 0.9, "samples": 5}}
        rows = compare_reports(baseline, candidate)
        self.assertAlmostEqual(rows["mrr"]["delta"], 0.1)
        self.assertEqual(rows["samples"]["delta"], 0.0)


class OfflineScoringTests(unittest.TestCase):
    def test_job_match_scores_a_stored_snapshot(self) -> None:
        dataset = [
            {
                "id": "jm-01",
                "job_description": "JD",
                "resume": "CV",
                "expected": {
                    "score_center": 60,
                    "must_match_skills": ["python"],
                    "must_missing_skills": ["rag"],
                },
            },
            {
                "id": "jm-02",
                "job_description": "JD",
                "resume": "CV",
                "expected": {"score_center": 40, "must_match_skills": ["python"]},
            },
        ]
        predictions = [
            {
                "id": "jm-01",
                "match_score": 66,
                "matched_skills": ["Python"],
                "missing_skills": ["RAG"],
                "error": None,
            },
            {"id": "jm-02", "error": "RuntimeError: provider unavailable"},
        ]
        metrics = score_job_match(dataset, predictions, tolerance=12)
        self.assertEqual(metrics["samples"], 2)
        self.assertEqual(metrics["schema_valid_rate"], 0.5)
        self.assertEqual(metrics["score_mae"], 6.0)
        self.assertEqual(metrics["score_within_tolerance"], 1.0)
        self.assertEqual(metrics["matched_skill_recall"], 1.0)
        self.assertEqual(metrics["missing_skill_recall"], 1.0)

    def test_job_match_honours_per_sample_tolerance(self) -> None:
        dataset = [
            {
                "id": "jm-01",
                "job_description": "JD",
                "resume": "CV",
                "expected": {"score_center": 50, "score_tolerance": 5},
            },
            {
                "id": "jm-02",
                "job_description": "JD",
                "resume": "CV",
                "expected": {"score_center": 50, "score_tolerance": 20},
            },
        ]
        predictions = [
            {"id": "jm-01", "match_score": 60, "matched_skills": [], "missing_skills": []},
            {"id": "jm-02", "match_score": 60, "matched_skills": [], "missing_skills": []},
        ]
        metrics = score_job_match(dataset, predictions, tolerance=12)
        # 10 points off: outside the tight sample, inside the wide one.
        self.assertEqual(metrics["score_within_tolerance"], 0.5)

    def test_interview_gradient_metrics_use_four_tiers(self) -> None:
        dataset = [
            {
                "id": "is-01",
                "question": "Q",
                "good_answer": "good",
                "medium_answer": "medium",
                "off_topic_answer": "off",
                "bad_answer": "bad",
            }
        ]
        predictions = [
            {
                "id": "is-01",
                "good_scores": [88, 90, 92],
                "medium_score": 62,
                "off_topic_score": 30,
                "bad_score": 15,
                "error": None,
            }
        ]
        metrics = score_interview(dataset, predictions)
        self.assertEqual(metrics["discrimination_rate"], 1.0)
        self.assertEqual(metrics["tier_order_rate"], 1.0)
        self.assertEqual(metrics["median_good_score"], 90.0)
        self.assertEqual(metrics["median_medium_score"], 62.0)
        self.assertEqual(metrics["median_off_topic_score"], 30.0)
        self.assertEqual(metrics["median_bad_score"], 15.0)

    def test_interview_metrics_degrade_without_optional_tiers(self) -> None:
        dataset = [{"id": "is-01", "question": "Q", "good_answer": "g", "bad_answer": "b"}]
        predictions = [{"id": "is-01", "good_scores": [80, 82], "bad_score": 20, "error": None}]
        metrics = score_interview(dataset, predictions)
        self.assertEqual(metrics["discrimination_rate"], 1.0)
        self.assertNotIn("tier_order_rate", metrics)
        self.assertNotIn("median_medium_score", metrics)


class DatasetIntegrityTests(unittest.TestCase):
    def test_job_match_samples_are_labelled(self) -> None:
        for sample in load_jsonl(EVALS_DIR / "job_match.jsonl"):
            self.assertTrue(sample["job_description"].strip())
            self.assertTrue(sample["resume"].strip())
            self.assertIn("score_center", sample["expected"])

    @unittest.skipUnless(
        (EVALS_DIR / "rag_corpus.jsonl").exists()
        and (EVALS_DIR / "rag_queries.jsonl").exists(),
        "no retrieval dataset checked in yet",
    )
    def test_rag_queries_reference_existing_chunks(self) -> None:
        corpus = load_jsonl(EVALS_DIR / "rag_corpus.jsonl")
        corpus_ids = {chunk["chunk_id"] for chunk in corpus}
        queries = load_jsonl(EVALS_DIR / "rag_queries.jsonl")
        self.assertTrue(queries)
        for query in queries:
            self.assertTrue(query["query"].strip())
            # relevant_chunk_ids is the legacy fallback; anchors are the
            # supported label format and must be present.
            self.assertTrue(query.get("relevant_anchors"), f"{query['id']} has no anchors")
            self.assertTrue(
                set(query.get("relevant_chunk_ids", [])) <= corpus_ids,
                f"{query['id']} references a chunk id that is not in the corpus",
            )
            # Anchors must resolve in the shipped corpus, otherwise a run either
            # crashes or silently scores nothing.
            resolved = resolve_relevant_chunks(query, corpus)
            self.assertTrue(resolved, f"{query['id']} resolves to no chunk")

    def test_anchor_labels_survive_a_corpus_rebuild(self) -> None:
        query = {"id": "rq-01", "relevant_anchors": ["local-minio-password"]}
        big = [
            {"chunk_id": "a", "text": "chunk about local-minio-password"},
            {"chunk_id": "b", "text": "unrelated chunk"},
        ]
        fine = [
            {"chunk_id": "a1", "text": "chunk about local"},
            {"chunk_id": "a2", "text": "minio-password tail"},
            {"chunk_id": "b", "text": "unrelated chunk"},
        ]
        self.assertEqual(resolve_relevant_chunks(query, big), ["a"])
        # A short anchor that straddles a chunk boundary resolves to nothing,
        # which must fail loudly rather than silently scoring zero recall.
        with self.assertRaises(ValueError):
            resolve_relevant_chunks(query, fine)

    def test_missing_anchors_fall_back_to_chunk_ids(self) -> None:
        corpus = [{"chunk_id": "c1", "text": "anything"}]
        self.assertEqual(
            resolve_relevant_chunks({"relevant_chunk_ids": ["c1"]}, corpus), ["c1"]
        )

    def test_interview_samples_have_contrasting_answers(self) -> None:
        for sample in load_jsonl(EVALS_DIR / "interview_scoring.jsonl"):
            self.assertTrue(sample["good_answer"].strip())
            self.assertTrue(sample["bad_answer"].strip())
            self.assertNotEqual(sample["good_answer"], sample["bad_answer"])
            # Optional tiers make the four-level ordering measurable; when a
            # sample ships one it must be a different answer, not a copy.
            for key in ("medium_answer", "off_topic_answer"):
                if key in sample:
                    self.assertTrue(sample[key].strip())
                    self.assertNotIn(
                        sample[key], {sample["good_answer"], sample["bad_answer"]}
                    )

    def test_load_jsonl_skips_comments_and_rejects_broken_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.jsonl"
            path.write_text(
                "# a comment\n\n" + json.dumps({"id": "a"}) + "\n", encoding="utf-8"
            )
            self.assertEqual(len(load_jsonl(path)), 1)
            path.write_text("{not json}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_jsonl(path)


class PromotionGuardTests(unittest.TestCase):
    """The baseline is what CI diffs against, so promoting must be deliberate."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.reports = Path(directory.name)
        patcher = mock.patch("evals.core.reporting.REPORTS_DIR", self.reports)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_latest(
        self,
        suite: str,
        *,
        mode: str = "live",
        metrics: dict[str, object] | None = None,
    ) -> Path:
        path = self.reports / f"{suite}.latest.json"
        path.write_text(
            json.dumps(
                {
                    "suite": suite,
                    "metadata": {"suite": suite, "mode": mode},
                    "metrics": metrics if metrics is not None else {"samples": 10},
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_a_complete_live_run_has_no_blockers(self) -> None:
        report = {
            "metadata": {"mode": "live"},
            "metrics": {
                "samples": 25,
                "scored_samples": 25,
                "schema_valid_rate": 1.0,
            },
        }
        self.assertEqual(promotion_blockers(report), [])

    def test_an_offline_rescore_is_blocked(self) -> None:
        # run_all --compare overwrites <suite>.latest.json, so the newest report
        # is often a free re-score rather than a fresh provider run.
        report = {"metadata": {"mode": "offline"}, "metrics": {"samples": 25}}
        blockers = promotion_blockers(report)
        self.assertEqual(len(blockers), 1)
        self.assertIn("mode='offline'", blockers[0])

    def test_partial_coverage_is_blocked(self) -> None:
        report = {
            "metadata": {"mode": "live"},
            "metrics": {"samples": 25, "scored_samples": 3},
        }
        blockers = promotion_blockers(report)
        self.assertTrue(any("3/25" in blocker for blocker in blockers))

    def test_schema_failures_are_blocked(self) -> None:
        report = {
            "metadata": {"mode": "live"},
            "metrics": {"schema_valid_rate": 0.9375},
        }
        blockers = promotion_blockers(report)
        self.assertTrue(any("schema_valid_rate" in b for b in blockers))

    def test_promote_refuses_and_keeps_the_old_baseline(self) -> None:
        baseline = self.reports / "job_match.baseline.json"
        baseline.write_text('{"metrics": {}}', encoding="utf-8")
        self.write_latest("job_match", mode="offline")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(promote_suite("job_match"), 2)
        self.assertEqual(baseline.read_text(encoding="utf-8"), '{"metrics": {}}')

    def test_promote_force_overwrites_the_baseline(self) -> None:
        latest = self.write_latest("job_match", mode="offline")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(promote_suite("job_match", force=True), 0)
        baseline = self.reports / "job_match.baseline.json"
        self.assertEqual(baseline.read_text(encoding="utf-8"), latest.read_text(encoding="utf-8"))

    def test_promote_without_any_run_says_what_to_do(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(promote_suite("job_match"), 2)
        self.assertIn("--live", output.getvalue())


if __name__ == "__main__":
    unittest.main()
