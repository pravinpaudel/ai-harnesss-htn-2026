# Checkpoint 2 — known-corpus triage

Checkpoint 2 asks for every failure on the known corpus to be sorted into one class — extraction
gap, normalization issue, retrieval miss, calculation issue, policy/citation failure, or benchmark
ambiguity — and for the owning layer to be fixed, never the question.

## Where the failures went

| Case | Class | Resolution |
|---|---|---|
| FX03 (comparison), FX08 (currency ambiguity) | extraction gap | Fixed by the Phase 2 parser work (`docs/phase2-rehearsal.md`). The fixture suite on real ingest went from 8/10 to **10/10**; no change was made for these two cases. |
| MIN02 (identification) | **benchmark ambiguity** | The suite demanded a citation inside WPM's Q1 2026 block. The answer key's own evidence column lists lines 206, 1494 and 1499 — the deep-dive thesis, the Q4 2025 analyst quote and the Q1 2026 summary — and the engine cited the first two. The report narrates the Antamina deal across three quarters (announced Q4 2025, closed Q1 2026, first full contribution Q2 2026) and again in the deep dive, so pinning one quarter was our error, not the engine's. |
| MIN04, MIN09, TEC02 | benchmark ambiguity | Surfaced once anchors were built from the key: each cites the same fact from a different part of the same quarter block, or from the company's own profile. Covered by the anchor rule below. |
| FIN03, MIN05, FIN05, FIN09 (intermittent) | run-to-run variation | These pass or fail between runs on citation anchors. A suite varies by about one case. |

## What changed

Only `evals/build_rbc_sample.py`. The engine and the parser were not touched for Checkpoint 2.

Anchors used to come from one guess — the quarter named in the key — which is why a correct answer
citing the key's own lines could fail. They now come from the key itself: every line its evidence
column lists (widened to the quarter block that holds it, or to its paragraph outside one), the
block of the quarter it names, and, for a claim spanning several quarters, the company profile's
summary paragraph. `evals/README.md` states the rule; `tests/unit/test_rbc_sample_anchors.py` pins it.

An engine change was tried first and reverted: choosing the cited period by how many clues its
narrative covers, rather than by match strength. It did not move MIN02 — every WPM quarter matches
about one clue — and nothing else justified it, so it is not in the branch.

## Scores

Both stored runs of the suite, re-scored against the corrected anchors, are **30/30** with the right
company on 30/30. Under the old anchors the same answers scored 28/30 and 29/30, the difference
being which evidence the case demanded, not what the engine answered.

Anchors cover a median of 2.1% of a report (at most 4.9%), so a right name cited from unrelated
evidence still fails. Questions that genuinely share evidence accept each other's citations — 7 of
28 same-company pairs — which is a property of the corpus, not a loosened test.

## Not done here

- `MIN02`'s answer still describes the deal as Q4 2025, the quarter it was announced in. The
  citation is valid and the company is right, so the suite passes it; a reader wanting the closing
  quarter gets the announcement instead. Choosing between an event's announcement and its completion
  needs evidence the store does not hold today.
- The fixture suite's 10/10 and the RBC 30/30 are one corpus. `docs/phase2-rehearsal.md` covers
  structural variation; a genuinely different document type is still unproven.
