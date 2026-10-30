import json
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from backend.app.ai.interview_engine import (
    AnswerEvaluation,
    OpenAIInterviewEngine,
    extract_json_object,
)

# The response shape that made eval sample is-09 fail with
# "ValidationError: ... follow_up_question Field required": a complete and
# parseable object that simply omits a required key.
IS_09_MISSING_FIELD = (
    '{"score": 88, "strengths": ["Mentions idempotency"], '
    '"improvements": ["No metrics"]}'
)
IS_09_REPAIRED = (
    '{"score": 88, "strengths": ["Mentions idempotency"], '
    '"improvements": ["No metrics"], '
    '"follow_up_question": "How would you verify idempotency under retries?"}'
)


class TransportError(Exception):
    """Stands in for openai.APIConnectionError / APIStatusError."""

# The response shape that made eval sample is-12 fail with
# "JSONDecodeError: Extra data: line 1 column 769 (char 768)": a valid object
# followed by prose the provider appended anyway.
IS_12_TRAILING_TEXT = (
    '{"score": 74, "strengths": ["Clear structure"], '
    '"improvements": ["Add measurable impact"], '
    '"follow_up_question": "How would you measure the result?"}'
    "\n\nNote: I scored this against a backend engineer baseline."
)


def engine_returning(content: str) -> OpenAIInterviewEngine:
    return engine_returning_sequence([content])


def engine_with_script(steps: list[str | Exception]) -> OpenAIInterviewEngine:
    """Engine playing back ``steps``: a response body, or an error to raise."""
    calls: list[dict[str, object]] = []

    class Completions:
        def create(self, **kwargs: object) -> SimpleNamespace:
            calls.append(kwargs)
            step = steps[min(len(calls) - 1, len(steps) - 1)]
            if isinstance(step, Exception):
                raise step
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=step))]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    engine = OpenAIInterviewEngine(client, "fake-model")
    engine.calls = calls  # type: ignore[attr-defined]
    return engine


def engine_returning_sequence(contents: list[str]) -> OpenAIInterviewEngine:
    """Engine whose provider returns ``contents`` in order, recording every call."""
    return engine_with_script(list(contents))


def evaluate(engine: OpenAIInterviewEngine) -> AnswerEvaluation:
    return engine.evaluate_answer(
        role="Software Engineer",
        job_description="Backend role",
        question="How do you make retries safe?",
        answer="With idempotency keys.",
        history=[],
        knowledge_context="",
    )


class JsonExtractionTests(unittest.TestCase):
    def test_trailing_text_after_json_is_ignored(self) -> None:
        # The regression itself: plain json.loads cannot read this payload.
        with self.assertRaises(json.JSONDecodeError):
            json.loads(IS_12_TRAILING_TEXT)

        evaluation = engine_returning(IS_12_TRAILING_TEXT).evaluate_answer(
            role="Software Engineer",
            job_description="Backend role",
            question="How do you handle retries?",
            answer="With exponential backoff.",
            history=[],
            knowledge_context="",
        )
        self.assertEqual(evaluation.score, 74)
        self.assertEqual(evaluation.follow_up_question, "How would you measure the result?")

    def test_only_the_first_object_is_used(self) -> None:
        payload = '{"score": 60, "strengths": [], "improvements": [], "follow_up_question": null} {"score": 10}'
        parsed = extract_json_object(payload)
        self.assertEqual(parsed["score"], 60)

    def test_markdown_fence_and_prose_are_stripped(self) -> None:
        payload = (
            "Sure, here is the evaluation:\n"
            '```json\n{"score": 88, "strengths": ["Depth"], '
            '"improvements": ["Brevity"], "follow_up_question": null}\n```'
        )
        self.assertEqual(extract_json_object(payload)["score"], 88)

    def test_plain_prose_around_the_object(self) -> None:
        self.assertEqual(
            extract_json_object('Here you go: {"answer": 1} hope that helps')["answer"], 1
        )
        self.assertEqual(
            extract_json_object('{"answer": 2} -- let me know if you want more.')["answer"], 2
        )
        self.assertEqual(extract_json_object('Result:\n{"answer": 3}\n(End)')["answer"], 3)

    def test_nested_json_is_parsed_whole(self) -> None:
        payload = (
            '{"score": 90, "strengths": ["a"], "improvements": ["b"], '
            '"follow_up_question": null, '
            '"meta": {"rubric": {"levels": [1, 2, 3]}, "tags": ["x", "y"]}}'
        )
        parsed = extract_json_object(payload)
        self.assertEqual(parsed["meta"]["rubric"]["levels"], [1, 2, 3])
        self.assertEqual(parsed["meta"]["tags"], ["x", "y"])

    def test_braces_inside_strings_do_not_truncate_the_object(self) -> None:
        payload = (
            '{"score": 70, "strengths": ["used {braces} in code"], '
            '"improvements": ["{ not JSON"], "follow_up_question": null}'
        )
        parsed = extract_json_object(payload)
        self.assertEqual(parsed["strengths"], ["used {braces} in code"])
        self.assertEqual(parsed["improvements"], ["{ not JSON"])

    def test_the_first_parseable_object_wins(self) -> None:
        # Documented limitation: the fallback takes the first object that
        # parses, so a JSON-looking snippet in prose ahead of the real payload
        # wins. Callers cannot fix that without knowing the schema, so the
        # clean-payload fast path (json.loads) is what production relies on.
        payload = 'He wrote {"draft": true} first, then answered: {"answer": 7}'
        self.assertEqual(extract_json_object(payload), {"draft": True})

    def test_plain_json_still_parses(self) -> None:
        payload = '{"score": 50, "strengths": [], "improvements": [], "follow_up_question": null}'
        self.assertEqual(engine_returning(payload).evaluate_answer(
            role="r", job_description="j", question="q", answer="a",
            history=[], knowledge_context="",
        ).score, 50)

    def test_response_without_an_object_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            extract_json_object("I am unable to answer that.")

    def test_schema_violation_is_rejected(self) -> None:
        out_of_range = '{"score": 120, "strengths": [], "improvements": [], "follow_up_question": null}'
        with self.assertRaises(ValidationError):
            engine_returning(out_of_range).evaluate_answer(
                role="r", job_description="j", question="q", answer="a",
                history=[], knowledge_context="",
            )

        missing_fields = '{"score": 70}'
        with self.assertRaises(ValidationError):
            engine_returning(missing_fields).evaluate_answer(
                role="r", job_description="j", question="q", answer="a",
                history=[], knowledge_context="",
            )

    def test_report_parsing_uses_the_same_path(self) -> None:
        payload = (
            '{"overall_score": 81, "summary": "Solid", "strengths": ["a"], '
            '"improvement_areas": ["b"], "recommendations": ["c"]}'
            " Let me know if you want more detail."
        )
        report = engine_returning(payload).create_report([])
        self.assertEqual(report.overall_score, 81)

    def test_evaluation_is_a_validated_model(self) -> None:
        evaluation = engine_returning(IS_12_TRAILING_TEXT).evaluate_answer(
            role="r", job_description="j", question="q", answer="a",
            history=[], knowledge_context="",
        )
        self.assertIsInstance(evaluation, AnswerEvaluation)


class SchemaRetryTests(unittest.TestCase):
    def test_missing_follow_up_question_is_repaired(self) -> None:
        # The regression itself: the payload parses, but a required key is gone.
        self.assertNotIn("follow_up_question", json.loads(IS_09_MISSING_FIELD))

        engine = engine_returning_sequence([IS_09_MISSING_FIELD, IS_09_REPAIRED])
        evaluation = evaluate(engine)

        self.assertEqual(evaluation.score, 88)
        self.assertEqual(
            evaluation.follow_up_question,
            "How would you verify idempotency under retries?",
        )
        self.assertEqual(engine.stats.calls, 2)
        self.assertEqual(engine.stats.retries, 1)
        self.assertEqual(engine.stats.schema_validation_failures, 1)
        self.assertEqual(engine.stats.retry_successes, 1)
        self.assertEqual(engine.stats.final_failures, 0)

        # The retry has to name the missing field and the type it expects.
        repair_prompt = engine.calls[1]["messages"][1]["content"]  # type: ignore[index]
        self.assertIn("Missing field(s): follow_up_question", repair_prompt)
        self.assertIn("follow_up_question (str | None)", repair_prompt)

    def test_retry_response_is_validated_again(self) -> None:
        out_of_range = (
            '{"score": 150, "strengths": [], "improvements": [], '
            '"follow_up_question": null}'
        )
        engine = engine_returning_sequence([IS_09_MISSING_FIELD, out_of_range])
        with self.assertRaises(ValidationError):
            evaluate(engine)
        self.assertEqual(engine.stats.calls, 2)
        self.assertEqual(engine.stats.retries, 1)
        self.assertEqual(engine.stats.retry_successes, 0)
        self.assertEqual(engine.stats.final_failures, 1)

    def test_at_most_one_retry(self) -> None:
        engine = engine_returning_sequence(
            [IS_09_MISSING_FIELD, IS_09_MISSING_FIELD, IS_09_REPAIRED]
        )
        with self.assertRaises(ValidationError):
            evaluate(engine)
        self.assertEqual(engine.stats.calls, 2)
        self.assertEqual(engine.stats.retries, 1)

    def test_value_errors_are_not_retried(self) -> None:
        out_of_range = (
            '{"score": 120, "strengths": [], "improvements": [], '
            '"follow_up_question": null}'
        )
        engine = engine_returning(out_of_range)
        with self.assertRaises(ValidationError):
            evaluate(engine)
        self.assertEqual(engine.stats.calls, 1)
        self.assertEqual(engine.stats.retries, 0)
        self.assertEqual(engine.stats.schema_validation_failures, 1)
        self.assertEqual(engine.stats.final_failures, 1)

    def test_json_failure_is_counted_separately(self) -> None:
        engine = engine_returning("I cannot provide JSON right now.")
        with self.assertRaises(RuntimeError):
            evaluate(engine)
        self.assertEqual(engine.stats.json_parsing_failures, 1)
        self.assertEqual(engine.stats.schema_validation_failures, 0)
        self.assertEqual(engine.stats.retries, 0)

    def test_no_retry_on_a_clean_response(self) -> None:
        engine = engine_returning(IS_09_REPAIRED)
        evaluate(engine)
        self.assertEqual(engine.stats.calls, 1)
        self.assertEqual(engine.stats.retries, 0)
        self.assertEqual(engine.stats.final_failures, 0)


class ProviderFailureTests(unittest.TestCase):
    def test_transport_error_is_not_a_parsing_failure(self) -> None:
        engine = engine_with_script([TransportError("connection reset")])
        with self.assertRaises(TransportError):
            evaluate(engine)
        self.assertEqual(engine.stats.calls, 1)
        self.assertEqual(engine.stats.provider_failures, 1)
        self.assertEqual(engine.stats.final_failures, 1)
        self.assertEqual(engine.stats.json_parsing_failures, 0)
        self.assertEqual(engine.stats.schema_validation_failures, 0)
        self.assertEqual(engine.stats.retries, 0)

    def test_empty_response_is_a_provider_failure(self) -> None:
        engine = engine_returning("")
        with self.assertRaises(RuntimeError):
            evaluate(engine)
        self.assertEqual(engine.stats.provider_failures, 1)
        self.assertEqual(engine.stats.json_parsing_failures, 0)
        self.assertEqual(engine.stats.schema_validation_failures, 0)

    def test_retry_transport_error_is_recorded(self) -> None:
        # Missing field -> retry -> the retry request dies in transit.
        engine = engine_with_script(
            [IS_09_MISSING_FIELD, TransportError("read timeout")]
        )
        with self.assertRaises(TransportError):
            evaluate(engine)
        self.assertEqual(engine.stats.calls, 2)
        self.assertEqual(engine.stats.retries, 1)
        self.assertEqual(engine.stats.schema_validation_failures, 1)
        self.assertEqual(engine.stats.provider_failures, 1)
        self.assertEqual(engine.stats.final_failures, 1)
        self.assertEqual(engine.stats.retry_successes, 0)
        self.assertEqual(engine.stats.json_parsing_failures, 0)
        self.assertGreaterEqual(engine.stats.retry_latency_seconds, 0.0)


class StatsLifecycleTests(unittest.TestCase):
    def test_counters_are_not_shared_between_engines(self) -> None:
        failing = engine_returning("no JSON here")
        clean = engine_returning(IS_09_REPAIRED)
        with self.assertRaises(RuntimeError):
            evaluate(failing)
        evaluate(clean)
        self.assertEqual(failing.stats.json_parsing_failures, 1)
        # The clean engine made its own single call and saw no failure of any kind.
        self.assertEqual(clean.stats.calls, 1)
        for name, value in clean.stats.as_dict().items():
            if name != "calls":
                self.assertEqual(value, 0, f"{name} leaked from another engine")

    def test_a_fresh_engine_starts_at_zero(self) -> None:
        stats = engine_returning(IS_09_REPAIRED).stats.as_dict()
        self.assertEqual(sum(value for value in stats.values()), 0)

    def test_reset_allows_reusing_an_engine_for_a_second_run(self) -> None:
        engine = engine_returning("no JSON here")
        with self.assertRaises(RuntimeError):
            evaluate(engine)
        engine.stats.reset()
        self.assertEqual(sum(engine.stats.as_dict().values()), 0)


if __name__ == "__main__":
    unittest.main()
