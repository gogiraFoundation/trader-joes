---
name: research-web
description: >-
  Search the web and open the result pages before answering an external
  factual question. Use when the user asks to research, look up, verify,
  cite sources, compare documentation, or check current fees, API behavior,
  papers, or market rules. Do not answer those questions from memory.
---

# Web research

External facts come from pages you open in this turn. Training knowledge and search snippets are leads, not sources.

`python -m tr_bot.managers research-query` and `research-agent` read the local ledger only. They do not search the web. Use them for this repo's trades, then still search when the question leaves the repo.

## Required steps

1. Write the claim that needs an outside source, in one sentence.
2. Call `WebSearch` at least once. Use a specific query (product, version, endpoint, or date when it matters). If the first page of results is off-topic, search again with different terms.
3. Choose up to four URLs. Prefer official docs, exchange rule pages, and primary papers. Use a blog or forum only when no primary page exists.
4. Call `WebFetch` on each chosen URL. Two pages is the minimum when search returns at least two. If fewer exist, fetch all of them.
5. Read the fetched markdown. Quote or paraphrase only text that fetch returned.
6. If a fetch is empty, blocked, or an error, say so and fetch the next result. Do not fill the gap from memory.
7. Answer with a **Sources** list: page title, URL, and the fact that page established.

## Source order

1. Official documentation for the system named in the question (Binance, ccxt, CoinMarketCap, CoinDesk, scikit-learn, FastAPI, pandas).
2. Standards, exchange rulebooks, and primary papers.
3. The repository (`tr_bot/`, `docs/`, `.env.example`) for how this bot behaves.
4. Secondary write-ups, labeled as secondary.

Search snippets, titles, and dates are not citations. A citation requires a fetched page.

## Answer shape

```markdown
## Findings
[What the opened pages say. One claim per sentence, each tied to a source.]

## Repo check
[What this codebase does, with file paths. Omit if the question is not about this repo.]

## Gaps
[Pages that failed to load, or claims you could not verify.]

## Sources
- [Title](URL) — fact this page established
```

Separate "from this repo" and "from the web". Do not merge them into one unqualified statement.

## Stop rules

- Do not submit orders, edit `.env`, or promote a strategy profile from a research pass.
- Do not invent a URL, a fee, an endpoint, or a paper title.
- Do not treat a cached or remembered doc as current until `WebFetch` returns it in this turn.
