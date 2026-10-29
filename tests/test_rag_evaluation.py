import unittest

from backend.app.ai.evaluation import recall_at_k, reciprocal_rank


class RagEvaluationTests(unittest.TestCase):
    def test_recall_at_k_measures_relevant_retrieval(self) -> None:
        self.assertAlmostEqual(
            recall_at_k(
                ["chunk-a", "chunk-c", "chunk-b"],
                {"chunk-a", "chunk-b"},
                k=2,
            ),
            0.5,
        )

    def test_reciprocal_rank_uses_first_relevant_result(self) -> None:
        self.assertAlmostEqual(
            reciprocal_rank(["irrelevant", "relevant", "also-relevant"], {"relevant"}),
            0.5,
        )
        self.assertEqual(reciprocal_rank(["irrelevant"], {"relevant"}), 0.0)

    def test_metrics_reject_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            recall_at_k(["a"], {"a"}, 0)
        with self.assertRaises(ValueError):
            reciprocal_rank([], set())


if __name__ == "__main__":
    unittest.main()
