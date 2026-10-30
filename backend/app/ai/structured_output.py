"""Turn a provider's free-form reply into a validated Pydantic model.

Every provider caller in the project needs the same three steps -- find the
JSON, validate it against a schema, and recover when the model merely forgot a
field -- so they live here once instead of being re-implemented per service.
The interview engine and the job-match service both go through
``StructuredCompletion``, which keeps one set of counters and one notion of
what counts as a failure.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from json import JSONDecodeError, JSONDecoder
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

BaseModelT = TypeVar("BaseModelT", bound=BaseModel)

logger = logging.getLogger(__name__)

JSON_FAILURE = "json_parsing_failure"
SCHEMA_FAILURE = "schema_validation_failure"
PROVIDER_FAILURE = "provider_failure"

# Scanning every '{' is worst case quadratic; a real reply never has this many.
MAX_CANDIDATES = 25


def _strip_fences(text: str) -> str:
    """Drop a surrounding ```json fence, which providers add despite the format."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _validates(schema: type[BaseModel], payload: Any) -> bool:
    """Pre-check used only to choose between candidates.

    The winner is still validated for real by the caller, so this never
    replaces validation -- it just stops a stray object in the prose from
    beating the payload.
    """
    try:
        schema.model_validate(payload)
    except ValidationError:
        return False
    return True


def extract_json_object(
    content: str, schema: type[BaseModel] | None = None
) -> dict[str, Any]:
    """Return the JSON object a provider meant, not the first one it wrote.

    A reply can carry the payload plus trailing prose even when
    ``response_format`` asks for JSON; eval sample ``is-12`` died with
    ``JSONDecodeError: Extra data`` on a response whose object was perfectly
    usable, which silently dropped the sample from the metrics.

    Nested objects, arrays and braces inside strings are the decoder's job.
    When ``schema`` is given, candidates are tried against it first, so a
    JSON-looking snippet in the prose cannot win over the real payload; the
    chosen object is still validated by the caller afterwards.
    """
    text = _strip_fences(content)
    try:
        parsed = json.loads(text)
    except JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and (schema is None or _validates(schema, parsed)):
        return parsed

    candidates: list[dict[str, Any]] = []
    decoder = JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text, index)
        except JSONDecodeError:
            continue
        if not isinstance(candidate, dict):
            continue
        if schema is not None and _validates(schema, candidate):
            return candidate
        candidates.append(candidate)
        if len(candidates) >= MAX_CANDIDATES:
            break

    if candidates:
        return candidates[0]
    if isinstance(parsed, dict):
        # Nothing better turned up; let the caller report the real problem.
        return parsed
    raise RuntimeError("Model response did not contain a JSON object.")


@dataclass
class CompletionStats:
    """Call accounting for one completion instance, so a flaky provider shows up.

    Instance state, never class state: every caller owns its own counters, so
    an evaluation run, an API request and a job-match analysis are counted
    independently.
    """

    calls: int = 0
    provider_failures: int = 0
    json_parsing_failures: int = 0
    schema_validation_failures: int = 0
    retries: int = 0
    retry_successes: int = 0
    final_failures: int = 0
    retry_latency_seconds: float = 0.0

    def reset(self) -> None:
        """Zero the counters so one instance can be reused for a second run."""
        self.__init__()  # type: ignore[misc]

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "provider_failures": self.provider_failures,
            "json_parsing_failures": self.json_parsing_failures,
            "schema_validation_failures": self.schema_validation_failures,
            "retries": self.retries,
            "retry_successes": self.retry_successes,
            "final_failures": self.final_failures,
            "retry_latency_seconds": round(self.retry_latency_seconds, 3),
        }


def _annotation_name(annotation: Any) -> str:
    return getattr(annotation, "__name__", None) or str(annotation)


def missing_fields(error: ValidationError) -> list[str]:
    """Required fields the model omitted -- the recoverable validation case."""
    names: list[str] = []
    for item in error.errors():
        if item.get("type") == "missing" and item.get("loc"):
            names.append(".".join(str(part) for part in item["loc"]))
    return names


def repair_instruction(schema: type[BaseModel], missing: list[str]) -> str:
    required = ", ".join(
        f"{name} ({_annotation_name(info.annotation)})"
        for name, info in schema.model_fields.items()
    )
    return (
        "Your previous response failed schema validation. "
        f"Missing field(s): {', '.join(missing)}. "
        f"Required fields: {required}. "
        "Return exactly one JSON object containing every required field, with no "
        "prose before or after it."
    )


class StructuredCompletion:
    """Extract, validate and retry once, for any provider client.

    ``send(system, user, extra)`` performs one request and returns the raw text;
    ``extra`` carries the repair instruction on a retry. Callers supply their
    own transport (OpenAI SDK, LangChain chain, ...) and all of them inherit
    the same parsing rules, the same retry discipline and the same accounting.
    """

    def __init__(
        self,
        send: Callable[[str, str, str | None], str],
        *,
        name: str = "completion",
        stats: CompletionStats | None = None,
    ) -> None:
        self._send = send
        self.name = name
        # Callers that need to report the counters (an eval run, say) can own
        # them; everyone else gets a fresh set.
        self.stats = stats if stats is not None else CompletionStats()

    def request(self, system: str, user: str, extra: str | None = None) -> str:
        """One provider request, counted as an attempt whatever the outcome.

        Transport, auth and quota errors are their own failure class; they must
        never land in the JSON or schema counters, which describe responses the
        provider actually returned.
        """
        self.stats.calls += 1
        try:
            content = self._send(system, user, extra)
        except Exception as error:  # noqa: BLE001 - re-raised, only counted here
            self.stats.provider_failures += 1
            self.stats.final_failures += 1
            logger.warning(
                "%s provider call failed (%s): %s", self.name, PROVIDER_FAILURE, error
            )
            raise
        if not content:
            self.stats.provider_failures += 1
            self.stats.final_failures += 1
            logger.warning(
                "%s provider returned an empty response (%s)",
                self.name,
                PROVIDER_FAILURE,
            )
            raise RuntimeError(f"{self.name} model returned an empty response.")
        return content

    def complete(
        self, system: str, user: str, schema: type[BaseModelT]
    ) -> BaseModelT:
        """Parse one response, then validate it, retrying once on a missing field."""
        content = self.request(system, user)
        try:
            payload = extract_json_object(content, schema)
        except RuntimeError as error:
            self.stats.json_parsing_failures += 1
            self.stats.final_failures += 1
            logger.warning(
                "%s response could not be parsed as JSON (%s): %s",
                self.name,
                JSON_FAILURE,
                error,
            )
            raise

        try:
            return schema.model_validate(payload)
        except ValidationError as error:
            missing = missing_fields(error)
            if not missing:
                # A wrong type or an out-of-range value is not a missing field;
                # asking again rarely fixes it, so fail fast.
                self.stats.schema_validation_failures += 1
                self.stats.final_failures += 1
                logger.warning(
                    "%s response failed schema validation (%s), not retrying: %s",
                    self.name,
                    SCHEMA_FAILURE,
                    error,
                )
                raise
            self.stats.schema_validation_failures += 1
            logger.warning(
                "%s response failed schema validation (%s), missing %s; retrying once",
                self.name,
                SCHEMA_FAILURE,
                missing,
            )
            return self._retry_once(system, user, schema, missing)

    def _retry_once(
        self,
        system: str,
        user: str,
        schema: type[BaseModelT],
        missing: list[str],
    ) -> BaseModelT:
        """One targeted second attempt, parsed and validated exactly like the first."""
        self.stats.retries += 1
        started = time.perf_counter()
        # The request itself is not wrapped: a provider error is not a parsing
        # error, even on the retry.
        content = self.request(system, user, repair_instruction(schema, missing))
        try:
            payload = extract_json_object(content, schema)
            model = schema.model_validate(payload)
        except RuntimeError as error:
            self.stats.final_failures += 1
            logger.warning(
                "%s retry failed to parse (%s): %s", self.name, JSON_FAILURE, error
            )
            raise
        except ValidationError as error:
            self.stats.final_failures += 1
            logger.warning(
                "%s retry failed schema validation (%s): %s",
                self.name,
                SCHEMA_FAILURE,
                error,
            )
            raise
        finally:
            self.stats.retry_latency_seconds += time.perf_counter() - started
        self.stats.retry_successes += 1
        logger.info("%s retry recovered the response", self.name)
        return model
