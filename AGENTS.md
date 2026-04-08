# AGENTS.md

This project should stay small and GitHub-first.

## Goals

- Serve a single researcher.
- Run primarily in GitHub Actions.
- Avoid local setup requirements beyond optional debugging.
- Prefer public scholarly APIs over scraping.
- Keep costs low and predictable.
- Never write recommendations back to Zotero automatically.

## Non-Goals

- No frontend.
- No web UI.
- No Docker.
- No database server.
- No agent loop.
- No full-text crawling.
- No embeddings unless there is a clear future justification.

## Coding Guidance

- Keep modules small and typed.
- Prefer explicit retry and graceful fallback around network calls.
- Preserve the one-batched-LLM-call design.
- Make local ranking useful when `llm_enabled` is false.
- Keep README instructions friendly for researchers who do not use Python often.
- Do not log secrets.
