# Embedding model, chunk size and embedding type evals

Retrieval-only sweep over the 63 dataset questions that have expected chunks
(`backend/evals/sweep.py`, raw results in `backend/evals/sweep-results.csv`,
192 configs). Search runs in memory; it matches pgvector within one question
(sections + nano: sweep hit@1 0.413 / MRR 0.547, `run_evals` 0.397 / 0.542).

One question moves a metric by 0.016, so treat gaps under ~0.05 as noise.

## Baseline drift

The 2026-10-05 baseline (hit@1 0.46, MRR 0.59) was on the Markdown corpus.
On today's PDF corpus the same setup scores hit@1 0.41, MRR 0.55: the PDF
switch cost about five points of retrieval.

## Results (full-size float vectors)

| Setup | Chunks | hit@1 | hit@3 | recall@3 | recall@5 | MRR | top-3 chars |
|---|---|---|---|---|---|---|---|
| **Current**: sections, v5-nano, no task | 37 | 0.413 | 0.683 | 0.579 | 0.631 | 0.547 | 1,630 |
| sections, v5-nano, **task adapters** | 37 | 0.508 | 0.825 | 0.676 | 0.765 | 0.664 | 2,111 |
| sections, v5-small, task | 37 | 0.476 | 0.810 | 0.644 | 0.755 | 0.643 | 2,032 |
| sections, v4, task | 37 | 0.556 | 0.794 | 0.663 | 0.775 | 0.678 | 1,993 |
| sections, v3, task | 37 | 0.508 | 0.683 | 0.595 | 0.701 | 0.596 | 2,120 |
| fixed-800-0, v5-nano, task | 35 | 0.603 | 0.762 | 0.610 | 0.733 | 0.688 | 2,446 |
| fixed-1600-0, v5-nano, task | 20 | 0.619 | 0.857 | 0.712 | 0.813 | 0.734 | 4,221 |
| fixed-3200-0, v5-nano, task | 12 | 0.683 | 0.889 | 0.802 | 0.890 | 0.771 | 7,205 |

## Findings

1. **Task adapters are the clearest win, at no cost.** The app sends no
   `task`, so queries and passages are embedded the same way. Sending
   `retrieval.query` / `retrieval.passage` lifts every chunking and every model
   (nano + sections: hit@3 +0.14, MRR +0.12). Same model, same 768 dims; it
   only needs a re-ingest.
2. **A bigger model doesn't pay off.** With adapters on, v5-small and v4 are
   within noise of v5-nano; v4 is 3.8B params and 2048 dims. v3 is worst.
3. **Bigger chunks score higher, but partly by covering more of the corpus.**
   At 3200 chars, top-3 returns ~7.2k chars, about a quarter of the corpus,
   and a fixed chunk gets credit for every section it overlaps (≥100 chars).
   This metric can't penalise that, so the extra context has to be checked
   with the answer judge. 400-char chunks are worse everywhere. Overlap
   doesn't help consistently.
4. **Keep full-size float vectors.** Matryoshka truncation to 256/128 dims
   and binary vectors both cost 5–15 points of hit@3. With 37 chunks, storage
   savings are irrelevant.

## Next step

Wire task adapters into settings/ingestion, re-ingest, and run
`run_evals --judge` on **sections + task** and **fixed-1600-0 + task**, to
see whether the extra context in the larger chunks improves answers or just
inflates tokens.
