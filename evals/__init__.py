"""Offline evaluation harness for the AI interview assistant.

The harness is split into three suites (job match, RAG retrieval, interview
scoring). Each suite can run in two modes:

- ``live``: calls the configured provider once, stores raw predictions as a
  snapshot under ``evals/snapshots/`` and writes a metrics report.
- ``offline``: recomputes every metric from an existing snapshot. No provider
  call, no token cost, safe to run in CI.

Because metrics are pure functions of the snapshot, changing a threshold, a
metric or a label never requires paying for API calls again.
"""
