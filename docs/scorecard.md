# Release scorecard

Checked 2026-09-20T00:12:50+00:00 from a clean Docker Compose install (`evals/release_check.py`).

## Frozen for this release

| What | Version |
|---|---|
| Contract | 1.2 |
| Parser | 0.1.0 |
| Prompt | answer_v1 (`4365ada8982e28fd`) |
| Schema | `f2d657559f447850` |
| Model | gpt-5.6-luna |
| Embeddings | text-embedding-3-small |
| Commit | `945c122` |

## Evaluation

| Suite | Passed | Citations | Failures |
|---|---|---|---|
| Contract fixture | 9/10 | all verified | FX02 |
| RBC sample (known corpus) | 30/30 | all verified | none |
| RBC sample (restructured corpus) | 27/30 | all verified | FIN02, FIN05, MIN04 |

## Install and interfaces

- **Clean install:** tables 15, engine_role reads evidence, writes only its own audit rows, seconds 11.
- **`htn db init`:** schema_created False, idempotent True, seconds 1.
- **MCP ingest:** documents 3, findings 10, tools ['financialDataRetrieval'], tool_called financialDataRetrieval, arguments {}, second_run_reused_version True, seconds 26.
- **Known corpus:** documents 3, facts 1681, findings 10, expected_facts 73/73, seconds 23.
- **HTTP API:** healthz ok, status answered, citations 1, tool_events_recorded 7, conflicts 10, entities 19, table_coverage 0.647, seconds 5.
- **Trace replay:** run_id b971dc62-e55e-4beb-b3c0-ea20760bff5d, steps 12, kinds ['llm', 'policy', 'tool_call', 'verify'], seconds 1.
