import json
import unittest
from unittest import mock

from pydantic import ValidationError

from backend.app.ai.structured_output import (
    CompletionStats,
    StructuredCompletion,
    extract_json_object,
)
from schemas.job_match import JobMatchAnalysis

# A complete job-match analysis, and the same one with a field forgotten --
# the shape that used to fail the whole call with no second chance.
JOB_MATCH = {
    "match_score": 78,
    "matched_skills": ["Python"],
    "missing_skills": ["Kubernetes"],
    "strengths": ["Solid backend experience"],
    "gaps": ["No container orchestration"],
    "resume_improvement_tips": ["Add measured impact"],
    "interview_questions": ["How would you scale this service?"],
}
JOB_MATCH_MISSING_FIELD = {
    key: value for key, value in JOB_MATCH.items() if key != "interview_questions"
}


class SchemaAwareExtractionTests(unittest.TestCase):
    def test_a_json_snippet_in_prose_no_longer_wins(self) -> None:
        # With a schema, the draft object is skipped and the real payload wins.
        content = (
            'Let me draft {"match_score": 1} first. '
            f'Final answer: {json.dumps(JOB_MATCH)}'
        )
        self.assertEqual(extract_json_object(content, JobMatchAnalysis)["match_score"], 78)

    def test_without_a_schema_the_first_object_still_wins(self) -> None:
        content = "draft {\"a\": 1} then {\"b\": 2}"
        self.assertEqual(extract_json_object(content), {"a": 1})

    def test_no_candidate_validates_falls_back_to_the_first(self) -> None:
        # The caller still has to report the real problem, so a wrong payload
        # must not be silently replaced by a different wrong payload.
        content = '{"match_score": 120} then {"match_score": 95}'
        payload = extract_json_object(content, JobMatchAnalysis)
        self.assertEqual(payload["match_score"], 120)
        with self.assertRaises(ValidationError):
            JobMatchAnalysis.model_validate(payload)

    def test_trailing_prose_is_still_tolerated(self) -> None:
        content = f"{json.dumps(JOB_MATCH)}\n\nLet me know if you want more detail."
        self.assertEqual(
            extract_json_object(content, JobMatchAnalysis)["match_score"], 78
        )

    def test_response_without_an_object_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            extract_json_object("I cannot answer that.", JobMatchAnalysis)


def completion_returning(contents: list[str], name: str = "test") -> StructuredCompletion:
    """Completion whose transport replays ``contents`` and records extra prompts."""
    calls: list[str | None] = []

    def send(_system: str, _user: str, extra: str | None) -> str:
        calls.append(extra)
        index = min(len(calls) - 1, len(contents) - 1)
        return contents[index]

    completion = StructuredCompletion(send, name=name)
    completion.extras = calls  # type: ignore[attr-defined]
    return completion


def analyze(completion: StructuredCompletion) -> JobMatchAnalysis:
    return completion.complete("system", "user", JobMatchAnalysis)


class StructuredCompletionTests(unittest.TestCase):
    def test_clean_response_needs_no_retry(self) -> None:
        completion = completion_returning([json.dumps(JOB_MATCH)])
        self.assertEqual(analyze(completion).match_score, 78)
        self.assertEqual(completion.stats.calls, 1)
        self.assertEqual(completion.stats.retries, 0)

    def test_missing_field_is_repaired(self) -> None:
        completion = completion_returning(
            [json.dumps(JOB_MATCH_MISSING_FIELD), json.dumps(JOB_MATCH)]
        )
        self.assertEqual(analyze(completion).match_score, 78)
        self.assertEqual(completion.stats.retries, 1)
        self.assertEqual(completion.stats.retry_successes, 1)
        self.assertEqual(completion.stats.final_failures, 0)
        self.assertIn("Missing field(s): interview_questions", completion.extras[1])

    def test_value_errors_are_not_retried(self) -> None:
        out_of_range = dict(JOB_MATCH, match_score=120)
        completion = completion_returning([json.dumps(out_of_range)])
        with self.assertRaises(ValidationError):
            analyze(completion)
        self.assertEqual(completion.stats.calls, 1)
        self.assertEqual(completion.stats.retries, 0)
        self.assertEqual(completion.stats.schema_validation_failures, 1)

    def test_at_most_one_retry(self) -> None:
        completion = completion_returning(
            [json.dumps(JOB_MATCH_MISSING_FIELD)] * 3,
        )
        with self.assertRaises(ValidationError):
            analyze(completion)
        self.assertEqual(completion.stats.calls, 2)
        self.assertEqual(completion.stats.retries, 1)

    def test_unparsable_response_is_a_json_failure(self) -> None:
        completion = completion_returning(["Sorry, no JSON this time."])
        with self.assertRaises(RuntimeError):
            analyze(completion)
        self.assertEqual(completion.stats.json_parsing_failures, 1)
        self.assertEqual(completion.stats.schema_validation_failures, 0)

    def test_transport_error_is_a_provider_failure(self) -> None:
        class TransportError(Exception):
            pass

        def send(_system: str, _user: str, _extra: str | None) -> str:
            raise TransportError("connection reset")

        completion = StructuredCompletion(send, name="job_match")
        with self.assertRaises(TransportError):
            analyze(completion)
        self.assertEqual(completion.stats.provider_failures, 1)
        self.assertEqual(completion.stats.final_failures, 1)
        self.assertEqual(completion.stats.json_parsing_failures, 0)

    def test_empty_response_is_a_provider_failure(self) -> None:
        completion = completion_returning([""])
        with self.assertRaises(RuntimeError):
            analyze(completion)
        self.assertEqual(completion.stats.provider_failures, 1)
        self.assertEqual(completion.stats.json_parsing_failures, 0)

    def test_counters_are_per_instance(self) -> None:
        failing = completion_returning(["no JSON"])
        clean = completion_returning([json.dumps(JOB_MATCH)])
        with self.assertRaises(RuntimeError):
            analyze(failing)
        analyze(clean)
        self.assertEqual(failing.stats.json_parsing_failures, 1)
        for name, value in clean.stats.as_dict().items():
            if name != "calls":
                self.assertEqual(value, 0, f"{name} leaked from another instance")

    def test_reset_clears_the_counters(self) -> None:
        completion = completion_returning(["no JSON"])
        with self.assertRaises(RuntimeError):
            analyze(completion)
        completion.stats.reset()
        self.assertEqual(sum(completion.stats.as_dict().values()), 0)

    def test_stats_snapshot_shape(self) -> None:
        self.assertEqual(
            set(CompletionStats().as_dict()),
            {
                "calls",
                "provider_failures",
                "json_parsing_failures",
                "schema_validation_failures",
                "retries",
                "retry_successes",
                "final_failures",
                "retry_latency_seconds",
            },
        )


class JobMatchServiceTests(unittest.TestCase):
    """The job-match service must inherit the same recovery, not just the SDK."""

    def _analyze(self, contents: list[str]) -> tuple[dict, int]:
        import services.job_match_service as service

        calls: list[list[object]] = []

        class FakeModel:
            def invoke(self, messages: list[object]) -> object:
                calls.append(messages)
                index = min(len(calls) - 1, len(contents) - 1)
                return type("Reply", (), {"content": contents[index]})()

        class FakeChatOpenAI:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

            def invoke(self, messages: list[object]) -> object:
                return FakeModel().invoke(messages)

        with mock.patch.object(service, "ChatOpenAI", FakeChatOpenAI):
            result = service.analyze_job_match(
                "Backend role", "A resume", api_key="k", base_url="http://x", model_name="m"
            )
        return result, len(calls)

    def test_clean_response(self) -> None:
        result, calls = self._analyze([json.dumps(JOB_MATCH)])
        self.assertEqual(result["match_score"], 78)
        self.assertEqual(calls, 1)

    def test_trailing_prose_is_tolerated(self) -> None:
        result, calls = self._analyze(
            [f"{json.dumps(JOB_MATCH)}\n\nHope this helps!"]
        )
        self.assertEqual(result["match_score"], 78)
        self.assertEqual(calls, 1)

    def test_missing_field_triggers_one_retry(self) -> None:
        result, calls = self._analyze(
            [json.dumps(JOB_MATCH_MISSING_FIELD), json.dumps(JOB_MATCH)]
        )
        self.assertEqual(result["match_score"], 78)
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
