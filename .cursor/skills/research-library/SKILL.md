---
name: research-library
description: >-
  Compare libraries and check current API docs by searching and opening the
  project pages. Use when the user asks which package to add, how an API
  works, what changed in a version, or whether ccxt, scikit-learn, pandas,
  FastAPI, httpx, or a data-vendor SDK matches this repo.
---

# Library and API research

Follow `.cursor/skills/research-web/SKILL.md`. Open the project's own docs before recommending a dependency or an API call.

## Required pages

For each library or API under consideration:

1. `WebSearch` for `<name> official documentation` and `<name> changelog` or `release notes`.
2. `WebFetch` the docs page that describes the call or feature you would use.
3. `WebFetch` the changelog or release page when the question mentions a version, a breaking change, or "latest".
4. Read `requirements.txt` and the call site in `tr_bot/` that already depends on it.

If the docs site does not appear in search results, search once more for the repository README on the project's host. Do not substitute a tutorial until those two searches fail, and label the tutorial as secondary.

## What to extract from the fetched page

- The exact function, endpoint, or config key, copied from the page.
- The version or release date shown on the page. If the page shows none, write "version not shown".
- License, maintenance signal (recent release vs archived), and any security note the page states.
- Install constraint that would fit `requirements.txt` (Python 3.12+).

## Recommendation

Use a short matrix, then one recommendation. The deciding constraint is a sentence after the table.

| Option | Docs page fetched | Maintenance | Fit to `tr_bot/` |
| --- | --- | --- | --- |
| A | URL | | |
| B | URL | | |

Prefer a library already in `requirements.txt` when the fetched docs show it can do the job.

## Stop rules

- Do not add a dependency, change `requirements.txt`, or rewrite a client in the research turn unless the user asked to implement it.
- Do not invent parameters that were not on the fetched page.
- Pinned versions in this repo win over "latest" on a webpage until the user asks to bump them.
