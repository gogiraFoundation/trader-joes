---
name: insecure-defaults
description: >
  Audit a codebase for insecure default configuration: fallback secrets, default
  credentials, fail-open switches, weak crypto, permissive access, and debug leakage.
  Parallel category sweeps collect candidates; a refuting verifier traces each one to a
  security decision before it is reported. Use when asked to "find insecure defaults",
  "fail-open audit", "SECRET_KEY fallback", "default credentials", or review settings
  for hardcoded secrets and permissive defaults.
---

# Insecure defaults audit (Cursor adaptation)

Upstream is the Trail of Bits Claude Code plugin `plugins/insecure-defaults` in
[trailofbits/skills](https://github.com/trailofbits/skills). That plugin drives a
Claude **Workflow** (`upstream/audit-pipeline.js`). Cursor has no Workflow tool, so
this skill keeps the same corpora and refute rules; the agent performs the phases
itself. Do not invent findings. Do not open, read, or commit `.env*`, `*.key`, or
SSH private keys.

## Scope

Argument is a file or directory (default `.`). Whatever you point at is the target.
Exclusions (fixtures, docs, vendored code) apply only outside the named scope.

## Categories (load only the matching corpus)

| Category | Corpus |
| --- | --- |
| Fallback secrets | `references/fallback-secrets.md` + `.json` |
| Default credentials | `references/default-credentials.md` + `.json` |
| Fail-open switches | `references/fail-open-security.md` + `.json` |
| Weak crypto | `references/weak-crypto.md` + `.json` |
| Permissive access | `references/permissive-access.md` + `.json` |
| Debug leakage | `references/debug-features.md` + `.json` |

If `references/` is missing or empty, **stop and say so**. Do not audit without the corpus.

## Phases

1. **Recon** — Classify scope; note stack and deploy manifests (Compose, K8s, Procfile).
2. **Discover** — One sweep per category using that category's seed patterns and stack-derived idioms (`os.getenv(k, default)`, `env.get(k) or 'x'`, `${VAR:-default}`, Django `SECRET_KEY`, etc.). Grep only; do not judge yet. Dedup on `category:file:line`.
3. **Verify** — For each candidate, start at `refuted: true` and clear only if all pass:
   1. File reachable in production?
   2. Is the insecure value the one that runs? (Configurable must fail-open; unconditional cannot be refuted here.)
   3. Is the value actually insecure?
   4. Does it reach a security decision? Cite the sink.
   5. For configurable only: does deploy always supply the var? Partial/unknown counts as reachable.
   Incomplete trace = refuted.
4. **Report** — Severity, remediation, coverage gaps (empty derived-pattern lists).

## Output

- Findings with file:line, category, why it was not refuted, remediation.
- Or `no-candidates` / `no-findings-confirmed` with an explicit note that absence of matches is not proof of safety.
- Never modify application code unless the user explicitly asks for fixes after the report.

## Scripts / network

- Upstream `workflows/audit.js` is Claude Workflow metadata only; do not execute it here.
- Upstream tests use local `grep` via `child_process` (not vendored). No exchange or market-data API calls.
