# Examples

Standalone demos. Nothing here is part of the harness: no compose service, test or CLI command
references this directory, and `docker compose up` does not start any of it. Treat these as
throwaway references — the harness's own interfaces are the `htn` CLI and the API in `app/api/`.

| Example | What it is | How to run |
|---|---|---|
| `simple_python_example/` | A one-file FastAPI chat UI that talks to the MCP server and OpenAI directly, bypassing the evidence store, the citation verifier and the policy gate. Answers it produces are not verified against sources. | `uv run python examples/simple_python_example/app.py` (serves on :8000) |
