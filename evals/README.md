# Evaluation harness

Fixed sample sets and reusable runners for the three AI features. The goal is a
number you can quote, not a demo you can click:

> "Retrieval recall@5 went from 0.72 to 0.88 after switching the embedding
> provider" is a claim you can defend in an interview. "It works well" is not.

## Design

Each suite has two modes, and the split is what keeps it cheap:

| Mode | What it does | Cost |
|---|---|---|
| `--live` | Calls the provider **once**, stores raw predictions in `snapshots/` | tokens |
| offline (default) | Recomputes every metric from a snapshot | free |

Metrics are pure functions of the snapshot, so changing a threshold, a metric or
a human label never requires paying for API calls again. Offline mode also means
CI can validate the whole harness without secrets.

## Suites

| Suite | Runner | Metrics |
|---|---|---|
| `job_match` | `run_job_match.py` | `schema_valid_rate`, `score_mae`, `score_within_tolerance`, `matched_skill_recall`, `missing_skill_recall` |
| `rag_retrieval` | `run_rag_retrieval.py` | `recall@1`, `recall@3`, `recall@5`, `mrr` |
| `interview_scoring` | `run_interview_scoring.py` | `schema_valid_rate`, `discrimination_rate`, `mean_gap`, `median_good_score`, `score_spread_median`, `tier_order_rate` |

`job_match` scores each sample against its own `expected.score_tolerance` (a vague
JD is judged with a wider window than a detailed one) and falls back to
`--tolerance` when the sample does not set one.

`interview_scoring` scores every tier a sample provides: `good_answer` (repeated,
for stability), `medium_answer`, `off_topic_answer` and `bad_answer`.
`tier_order_rate` is the share of questions where `good > medium > max(off-topic,
wrong)`; a scorer that only knows "great" and "terrible" cannot pass it.

`rag_retrieval` scores the retrieval half only, with exact cosine ranking over a
fixed corpus. Production queries run through pgvector HNSW, an approximate
index, so these numbers are the achievable upper bound for the same vectors.
Use them to compare embedding models, chunk sizes and overlaps, and treat any
drop in production as an ANN recall question.

## Usage

```bash
conda activate ai-job-assistant

# 1. Refresh predictions against the configured providers (costs tokens).
python -m evals.run_job_match --live
python -m evals.run_rag_retrieval --live
python -m evals.run_interview_scoring --live --repeats 3

# 2. Promote the first good run as the baseline you will diff against.
#    Promotion is refused unless <suite>.latest.json is a complete live run:
#    'run_all --compare' overwrites it with a free offline re-score, and
#    promoting that would make the baseline forget where the numbers came from.
python -m evals.compare --suite job_match --promote
python -m evals.compare --suite rag_retrieval --promote
python -m evals.compare --suite interview_scoring --promote

# 3. Change something (prompt, model, chunk size), re-run live, then diff.
python -m evals.run_rag_retrieval --live --notes "chunk 1000 -> 600"
python -m evals.compare --suite rag_retrieval

# 4. Re-score an old snapshot with new labels, for free. This overwrites
#    <suite>.latest.json, so re-run step 1 before promoting anything.
python -m evals.run_job_match --snapshot evals/snapshots/job_match.deepseek-chat.latest.jsonl

# 5. Chunk-size experiment. The corpus is built with the production splitter,
#    and labels are content anchors, so both chunkings are scored against the
#    same gold text.
python -m evals.build_corpus --chunk-size 600 --overlap 80 --out evals/datasets/rag_corpus_600.jsonl
python -m evals.run_rag_retrieval --live --corpus evals/datasets/rag_corpus_600.jsonl --notes "chunk 600/80"
python -m evals.compare --suite rag_retrieval

# 6. Embedding model experiment (env var overrides the configured model).
EMBEDDING_MODEL=qwen3.7-text-embedding python -m evals.run_rag_retrieval --live --compare

# 5. Everything at once, offline (what CI runs).
python -m evals.run_all --compare
```

`--check` in CI fails when a metric regresses beyond `--min-delta`:

```bash
python -m evals.compare --suite rag_retrieval --check --min-delta -0.02
```

## Layout

```
evals/
  core/          datasets, metrics, reporting, snapshots (no model calls)
  datasets/      labelled samples - edit these
  run_*.py       one entry point per suite
  compare.py     baseline / candidate diff and --promote
  reports/       <suite>.latest.json, <suite>.baseline.json, timestamped history
  snapshots/     raw provider predictions, one file per suite and model
```

## Adding samples

Seed samples live in `evals/datasets/`; the current composition and labelling
rules are documented in `datasets/README.md`.

- `job_match.jsonl`: `job_description`, `resume`, and `expected`
  (`score_center` 0-100, `score_tolerance`, `must_match_skills`,
  `must_missing_skills`). **The seeded scores are a starting point — overwrite
  them with your own judgement**, otherwise you are measuring agreement with
  numbers you do not believe.
- `rag_corpus.jsonl` / `rag_queries.jsonl`: corpus chunks plus queries. The
  corpus is built from the documents in `evals/raw_docs/` by
  `evals/build_corpus.py`, which reuses the production `split_text`; pass
  `--source` to point at other files. **Label queries with
  `relevant_anchors`** (short quotes from the source text), not with
  `relevant_chunk_ids`: chunk ids silently point at different text as soon as
  the chunk size changes, which makes a chunk-size experiment meaningless.
  `resolve_relevant_chunks()` raises if an anchor matches nothing instead of
  silently scoring zero. `raw_docs/SOURCES.md` records where third-party
  documents came from and under which licence.
- `interview_scoring.jsonl`: one question with up to four answer tiers. The
  point is discrimination *and* gradient: a good scorer separates a strong
  answer from a thin one, not just from a wrong one.

`tests/test_evals_offline.py` guards dataset integrity and the metric maths, so
a typo in a sample fails CI instead of silently skewing a baseline.

## Known limits

- Seed samples are small; treat early numbers as smoke signals, not benchmarks.
- Live runs are single-shot, so variance is not fully characterised. The
  interview suite repeats a strong answer (`--repeats`) for a rough stability
  signal, and `score_spread_median` quantifies it.
- Answer quality (groundedness, citation correctness) is not yet scored
  automatically; that needs either an LLM judge or a labelled Q&A set.
