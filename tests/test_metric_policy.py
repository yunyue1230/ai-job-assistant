import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from evals import compare
from evals import provider_stats as evals_provider_stats
from evals.core.metrics import (
    METRIC_POLICY,
    evaluate_metric,
    evaluate_report,
    gating_regressions,
    policy_for,
    regression_threshold,
    warnings,
)

REPORTS_DIR = Path(__file__).resolve().parents[1] / "evals" / "reports"


class DirectionTests(unittest.TestCase):
    def test_higher_better_drops(self) -> None:
        verdict = evaluate_metric("mrr", 0.8, 0.6)
        self.assertEqual(verdict.decision, "regression")
        self.assertTrue(verdict.gating)
        self.assertAlmostEqual(verdict.threshold, 0.04)  # max(0.02, 0.05 * 0.8)

    def test_higher_better_improves(self) -> None:
        verdict = evaluate_metric("mrr", 0.6, 0.8)
        self.assertEqual(verdict.decision, "improved")
        self.assertFalse(verdict.gating)

    def test_higher_better_moves_within_the_band(self) -> None:
        verdict = evaluate_metric("mrr", 0.8, 0.78)
        self.assertEqual(verdict.decision, "ok")
        self.assertFalse(verdict.gating)

    def test_lower_better_rises(self) -> None:
        # The case the old --min-delta logic let through: a worse MAE is a
        # positive delta, so only a direction-aware check can catch it.
        verdict = evaluate_metric("score_mae", 12.56, 20.0)
        self.assertEqual(verdict.decision, "regression")
        self.assertTrue(verdict.gating)
        self.assertEqual(verdict.threshold, 3.0)  # max(3.0, 0.10 * 12.56)

    def test_lower_better_drops(self) -> None:
        verdict = evaluate_metric("score_mae", 20.0, 12.56)
        self.assertEqual(verdict.decision, "improved")
        self.assertFalse(verdict.gating)

    def test_lower_better_moves_within_the_band(self) -> None:
        verdict = evaluate_metric("score_mae", 12.56, 14.0)
        self.assertEqual(verdict.decision, "ok")


class ThresholdTests(unittest.TestCase):
    def test_zero_baseline_falls_back_to_the_absolute_band(self) -> None:
        policy = policy_for("score_spread_median")
        self.assertEqual(policy.rel_tolerance, None)
        self.assertEqual(regression_threshold(policy, 0.0), 2.0)

        verdict = evaluate_metric("score_spread_median", 0.0, 5.0)
        self.assertEqual(verdict.decision, "regression")
        self.assertTrue(verdict.gating)

    def test_zero_baseline_does_not_divide_by_zero(self) -> None:
        # A relative-only policy against a zero baseline cannot produce a
        # threshold, so the absolute band has to carry it.
        policy = METRIC_POLICY["mrr"]
        self.assertEqual(regression_threshold(policy, 0.0), 0.02)
        self.assertEqual(evaluate_metric("mrr", 0.0, 0.0).decision, "ok")
        self.assertEqual(evaluate_metric("mrr", 0.0, 0.5).decision, "improved")

    def test_ratio_and_score_scales_are_judged_differently(self) -> None:
        # One policy, two scales: 5% of the baseline, expressed in that
        # metric's own units -- points on 0-100, hundredths on 0-1.
        ratio = evaluate_metric("mrr", 0.667, 0.667)
        score = evaluate_metric("mean_gap", 70.0, 70.0)
        self.assertAlmostEqual(ratio.threshold, 0.0334, places=4)
        self.assertAlmostEqual(score.threshold, 3.5, places=4)
        self.assertAlmostEqual(ratio.threshold / 0.667, score.threshold / 70.0, places=3)
        # So 3 points of mean_gap is noise, while 0.04 of MRR is not.
        self.assertEqual(evaluate_metric("mean_gap", 70.0, 67.0).decision, "ok")
        self.assertEqual(evaluate_metric("mrr", 0.667, 0.627).decision, "regression")

    def test_relative_band_widens_with_the_baseline(self) -> None:
        # Same absolute drop: tolerated on a large score, not on a small one.
        large = evaluate_metric("mean_gap", 80.0, 76.5)  # 5% of 80 = 4.0
        small = evaluate_metric("mean_gap", 20.0, 16.5)  # 5% of 20 = 1.0 -> abs 3.0
        self.assertAlmostEqual(large.threshold, 4.0)
        self.assertAlmostEqual(small.threshold, 3.0)
        self.assertEqual(large.decision, "ok")
        self.assertEqual(small.decision, "regression")

    def test_unknown_metric_uses_the_min_delta_fallback(self) -> None:
        drop = evaluate_metric("brand_new_metric", 1.0, 0.9, min_delta=-0.02)
        self.assertEqual(drop.decision, "regression")
        self.assertTrue(drop.gating)
        self.assertEqual(drop.threshold, -0.02)
        self.assertEqual(drop.kind, "unknown")

    def test_unknown_metric_rise_is_not_a_regression(self) -> None:
        rise = evaluate_metric("brand_new_metric", 1.0, 1.5, min_delta=-0.02)
        self.assertEqual(rise.decision, "ok")
        self.assertFalse(rise.gating)

    def test_fallback_threshold_follows_min_delta(self) -> None:
        verdict = evaluate_metric("brand_new_metric", 1.0, 0.99, min_delta=-0.001)
        self.assertEqual(verdict.decision, "regression")


class ActionTests(unittest.TestCase):
    def test_sample_count_change_only_warns(self) -> None:
        verdict = evaluate_metric("samples", 25, 24)
        self.assertEqual(verdict.decision, "changed")
        self.assertEqual(verdict.action, "warn")
        self.assertFalse(verdict.gating)
        self.assertEqual(warnings([verdict]), ["samples"])

    def test_scored_samples_shrinking_only_warns(self) -> None:
        verdict = evaluate_metric("scored_samples", 26, 20)
        self.assertEqual(verdict.decision, "changed")
        self.assertFalse(verdict.gating)

    def test_info_metric_is_recorded_but_never_fatal(self) -> None:
        # A bad answer scoring far higher is a real signal, and still not gated.
        verdict = evaluate_metric("median_bad_score", 15.0, 30.0)
        self.assertEqual(verdict.action, "info")
        self.assertEqual(verdict.decision, "regression")
        self.assertFalse(verdict.gating)
        self.assertEqual(gating_regressions([verdict]), [])

    def test_missing_metric_is_unscored(self) -> None:
        verdict = evaluate_metric("mrr", None, 0.5)
        self.assertEqual(verdict.decision, "unscored")
        self.assertFalse(verdict.gating)

    def test_reported_metrics_all_have_a_policy(self) -> None:
        for suite in ("job_match", "rag_retrieval", "interview_scoring"):
            path = REPORTS_DIR / f"{suite}.baseline.json"
            if not path.exists():
                continue
            metrics = json.loads(path.read_text(encoding="utf-8"))["metrics"]
            for name in metrics:
                self.assertIsNotNone(policy_for(name), f"{suite}: {name} has no policy")


class CompareCliTests(unittest.TestCase):
    def _run(self, suite: str, baseline: dict, latest: dict, extra: list[str]) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kind, payload in (("baseline", baseline), ("latest", latest)):
                (root / f"{suite}.{kind}.json").write_text(
                    json.dumps({"suite": suite, "metadata": {}, "metrics": payload}),
                    encoding="utf-8",
                )
            buffer = StringIO()
            with mock.patch.object(
                compare, "report_path", lambda s, kind="latest": root / f"{s}.{kind}.json"
            ), mock.patch.object(
                sys, "argv", ["compare", "--suite", suite] + extra
            ), mock.patch.object(sys, "stdout", buffer):
                code = compare.main()
            return code, buffer.getvalue()

    def base(self) -> dict:
        return {
            "samples": 25,
            "schema_valid_rate": 1.0,
            "score_mae": 12.56,
            "median_bad_score": 15.0,
        }

    def test_gated_regression_fails_the_build(self) -> None:
        latest = dict(self.base(), score_mae=20.0)
        code, output = self._run("job_match", self.base(), latest, ["--check"])
        self.assertEqual(code, 1)
        self.assertIn("REGRESSION: score_mae", output)
        self.assertIn("score_mae", output)

    def test_warn_does_not_fail_the_build(self) -> None:
        latest = dict(self.base(), samples=24)
        code, output = self._run("job_match", self.base(), latest, ["--check"])
        self.assertEqual(code, 0)
        self.assertIn("WARNING: samples", output)
        self.assertNotIn("REGRESSION", output)

    def test_info_does_not_fail_the_build(self) -> None:
        latest = dict(self.base(), median_bad_score=30.0)
        code, output = self._run("job_match", self.base(), latest, ["--check"])
        self.assertEqual(code, 0)
        self.assertNotIn("REGRESSION", output)
        self.assertIn("median_bad_score", output)

    def test_improvement_passes(self) -> None:
        latest = dict(self.base(), score_mae=9.0)
        code, output = self._run("job_match", self.base(), latest, ["--check"])
        self.assertEqual(code, 0)
        self.assertIn("improved", output)

    def test_without_check_never_fails(self) -> None:
        latest = dict(self.base(), score_mae=40.0)
        code, _ = self._run("job_match", self.base(), latest, [])
        self.assertEqual(code, 0)

    def test_report_rows_carry_every_column(self) -> None:
        latest = dict(self.base(), score_mae=20.0)
        _, output = self._run("job_match", self.base(), latest, [])
        for column in ("baseline", "current", "delta", "threshold", "decision", "policy"):
            self.assertIn(column, output)
        self.assertIn("12.5600", output)
        self.assertIn("20.0000", output)


class ReportEvaluationTests(unittest.TestCase):
    def test_evaluate_report_covers_both_sides(self) -> None:
        verdicts = evaluate_report(
            {"metrics": {"mrr": 0.8, "samples": 10}},
            {"metrics": {"mrr": 0.6, "recall@5": 0.9}},
        )
        names = {verdict.name for verdict in verdicts}
        self.assertEqual(names, {"mrr", "samples", "recall@5"})
        by_name = {verdict.name: verdict for verdict in verdicts}
        self.assertTrue(by_name["mrr"].gating)
        self.assertEqual(by_name["samples"].decision, "unscored")


class NoiseCalibrationTests(unittest.TestCase):
    """Tolerances must clear the noise two live runs actually produced.

    Same code, same datasets, same providers; the only thing that moved is
    sampling. If a policy fails on this, CI fails on nothing.
    """

    # Live runs of 2026-10-30, 25 samples each.
    JOB_MATCH_RUN_1 = {
        "samples": 25,
        "schema_valid_rate": 1.0,
        "score_mae": 12.56,
        "score_within_tolerance": 0.76,
        "matched_skill_recall": 0.9867,
        "missing_skill_recall": 1.0,
    }
    JOB_MATCH_RUN_2 = {
        "samples": 25,
        "schema_valid_rate": 1.0,
        "score_mae": 12.0,
        "score_within_tolerance": 0.68,
        "matched_skill_recall": 0.9867,
        "missing_skill_recall": 1.0,
    }
    # Embeddings are deterministic: the second run reproduced the first exactly.
    RAG_RUN_1 = {
        "samples": 26,
        "scored_samples": 26,
        "recall@1": 0.3974,
        "recall@3": 0.7372,
        "recall@5": 0.8462,
        "mrr": 0.6667,
    }

    def _compare(self, baseline: dict, candidate: dict) -> dict[str, object]:
        verdicts = evaluate_report(
            {"metrics": baseline}, {"metrics": candidate}, -0.02
        )
        return {verdict.name: verdict for verdict in verdicts}

    def test_job_match_noise_is_not_a_regression(self) -> None:
        self.assertEqual(
            gating_regressions(
                evaluate_report(
                    {"metrics": self.JOB_MATCH_RUN_1},
                    {"metrics": self.JOB_MATCH_RUN_2},
                    -0.02,
                )
            ),
            [],
        )

    def test_tolerance_band_covers_the_observed_swing(self) -> None:
        # 0.76 -> 0.68 is two of 25 samples flipping, worth 0.08.
        verdicts = self._compare(self.JOB_MATCH_RUN_1, self.JOB_MATCH_RUN_2)
        swing = abs(
            self.JOB_MATCH_RUN_2["score_within_tolerance"]
            - self.JOB_MATCH_RUN_1["score_within_tolerance"]
        )
        self.assertGreaterEqual(verdicts["score_within_tolerance"].threshold, swing)

    def test_rag_is_reproducible_so_no_regression(self) -> None:
        self.assertEqual(
            gating_regressions(
                evaluate_report(
                    {"metrics": self.RAG_RUN_1}, {"metrics": self.RAG_RUN_1}, -0.02
                )
            ),
            [],
        )

    def test_a_real_drop_still_fails_after_widening(self) -> None:
        # Widening the band for noise must not swallow an actual regression.
        broken = dict(self.JOB_MATCH_RUN_1, score_within_tolerance=0.40)
        verdicts = self._compare(self.JOB_MATCH_RUN_1, broken)
        self.assertEqual(verdicts["score_within_tolerance"].decision, "regression")


class SharedReportingTests(unittest.TestCase):
    """run_all --compare and each runner go through compare.report_suite."""

    def _write(self, directory: Path, name: str, metrics: dict) -> None:
        path = directory / f"{name}.json"
        path.write_text(json.dumps({"metrics": metrics}), encoding="utf-8")

    def test_reports_verdicts_when_a_baseline_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "baseline", {"mrr": 0.8})
            self._write(root, "latest", {"mrr": 0.6})
            with mock.patch.object(
                compare, "report_path", lambda _suite, kind="latest": root / f"{kind}.json"
            ):
                with mock.patch("sys.stdout", new=StringIO()):
                    verdicts = compare.report_suite("rag_retrieval")
        self.assertIsNotNone(verdicts)
        self.assertEqual([v.name for v in verdicts], ["mrr"])
        self.assertTrue(verdicts[0].gating)

    def test_says_so_when_there_is_no_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                compare, "report_path", lambda _suite, kind="latest": root / f"{kind}.json"
            ):
                with mock.patch("sys.stdout", new=StringIO()) as out:
                    self.assertIsNone(compare.report_suite("rag_retrieval"))
        self.assertIn("no baseline", out.getvalue())


class ProviderStatsHistoryTests(unittest.TestCase):
    def test_collects_previous_live_runs(self) -> None:
        rows = evals_provider_stats.collect(REPORTS_DIR)
        interview = [row for row in rows if row["suite"] == "interview_scoring"]
        self.assertTrue(interview, "expected the recorded interview runs on disk")
        for row in interview:
            self.assertIn("calls", row["stats"])
            self.assertGreater(row["stats"]["calls"], 0)

    def test_reads_a_temporary_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "run.json").write_text(
                json.dumps(
                    {
                        "metadata": {
                            "suite": "job_match",
                            "created_at": "2026-10-30T00:00:00Z",
                            "mode": "live",
                            "provider_stats": {"calls": 25, "retries": 1},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (root / "noisy.json").write_text("not json", encoding="utf-8")
            rows = evals_provider_stats.collect(root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stats"]["calls"], 25)


if __name__ == "__main__":
    unittest.main()
