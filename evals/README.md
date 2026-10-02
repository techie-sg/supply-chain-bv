# DispatchDesk Week-1 RAG evaluation

Run from the repository root after configuring `backend/.env` (or the ignored
root `.env`):

```sh
uv run --project backend python evals/run_evals.py
```

Useful options:

```sh
uv run --project backend python evals/run_evals.py --limit 5 --top-k 5
uv run --project backend python evals/run_evals.py --resume
uv run --project backend python evals/run_evals.py --with-answer-generation
```

By default, the runner evaluates retrieval only: it calls the production
`service.embedder.embed_texts` and `service.vector_store.retrieve` functions,
and reports Recall@1, Recall@3, Recall@5, and MRR. It does not generate answers
or require Groq. Add `--with-answer-generation` to also run answer-quality
checks. The runner does not require the Gradio UI. Retrieval evaluation uses
Jina and Supabase. Results are written to
`evals/results/latest.json` and `evals/results/latest.csv`. The gold cases are
also exported to `evals/data/gold_dataset.csv` whenever the runner executes;
the JSON file remains the canonical versioned dataset.

The runner prints flushed retrieval progress for each case. The results CSV
contains the four retrieval `overall_metric` rows followed by `case_result`
rows with retrieved IDs, similarities, and rank. Nested lists in
the gold CSV are JSON-encoded inside their cells so they can be parsed back
without losing alternatives or chunk ID lists.

After each case finishes, the runner checkpoints the current JSON and CSV
results. If interrupted, run again with `--resume` (also available as
`--missing-only`) to keep successful cases and run cases that are missing or
failed. Without either flag, the run starts from the beginning and replaces the
results as cases complete. Resume requires the same dataset version,
retrieval-only setting, and top K as the saved run; change those by starting a
fresh run.

## Gold dataset

`data/gold_dataset.json` is version `1.0.2`, with 30 cases across diagnosis,
rain, batching, rider safety, approvals, customer communication, live-data
limits, out-of-domain questions, and adversarial prompts. Each case records
its question, expected/relevant chunk IDs, answer concept checks, forbidden
claims, and live/safety labels. `chunker.py` creates heading slugs for local
documents, but production `insert_chunks` stores section ordinals and the
Supabase retriever returns IDs such as `DD-WEATHER-001#1`. Gold IDs follow that
actual retriever format and are derived in the same sorted-file/section order
as ingestion. Reordering sections and reingesting changes numeric IDs, so
review labels after such corpus changes. The repository corpus README itself
is deliberately excluded from chunking, as it is by production ingestion.
The dataset references corpus revision `1.1 (2026-09-29)`.

Safety-sensitive cases specify `safety_required_include` and
`safety_prohibited` separately from general answer criteria, so a missing
diagnosis detail alone does not count as unsafe behavior.

Cases without corpus evidence (out-of-domain questions) are excluded from
retrieval hit-rate denominators. Other cases with a relevant evidence label
participate in retrieval evaluation. Expected IDs are editorial gold labels;
review them when corpus content or section headings change.

## Metrics

The default run reports only the following retrieval metrics:

- **Recall@K:** for each retrieval query, 1 when at least one relevant gold
  chunk appears among its first K results, otherwise 0; the reported score is
  the mean across eligible queries. The runner reports K = 1, 3, and 5.
- **MRR:** mean reciprocal rank of the first relevant result (`1 / rank`, or
  zero if no relevant result appears).

Pass `--with-answer-generation` to also calculate the answer metrics below:

- **Answer pass rate:** deterministic required-concept alternatives and
  prohibited-phrase checks. It is not an LLM semantic judge; valid paraphrases
  can fail if they miss configured phrases.
- **Live-data pass rate:** requires language stating that live information is
  unavailable or a live source is required, and fails when operational-looking
  values (counts, ETAs, durations, distances) are stated. It does not treat a
  static policy threshold as a current operational fact.
- **Safety compliance rate:** the case's required safety concepts must appear
  and prohibited phrases must not be asserted. Explicit negation is recognized
  in a short preceding text window, but this remains a deterministic heuristic.
- **Unsupported claim rate:** fraction of generated answers with a number and
  operational unit/label that is absent from the retrieved text. The detector
  is intentionally narrow and does not verify non-numeric claims.
- **Prohibited claim rate:** fraction of safety-sensitive responses asserting
  one of the case's explicitly prohibited phrases. Safety compliance also
  catches missing required safe behavior; prohibited claim rate does not.

The retrieval-only output includes per-query IDs, similarity values, first
relevant rank, retrieval metrics, git commit, and dataset version. With
`--with-answer-generation`, it also includes answer text and answer-evaluation
details. Metric values are not hard-coded.
`--top-k` controls the number of retrieval results recorded (the runner fetches
at least five so all standard Recall@K values remain available).
In answer-generation mode, the runner uses the same top three context chunks as
the production default. On Groq rate limits, it retries up to twice and honors
a provider `try again in ...s` hint when available.

## Limitations

The answer criteria are deterministic and lexical, so they can miss valid
paraphrases or pass an answer that is fluent but incomplete in an unlabelled
way. The unsupported-claim check only flags numeric operational assertions
that cannot be found in retrieved text; it cannot establish semantic support
for all claims. Review flagged cases and sample unflagged answers manually.
API/service errors are recorded per case and reported in the run summary;
failed calls are excluded from score denominators and the affected score is
unavailable if no cases succeeded.
