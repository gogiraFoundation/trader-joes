#!/usr/bin/env bash
# Audit installed packages. CVE-2026-4539 (pygments ReDoS): no release >2.19.2 on PyPI yet; remove --ignore-vuln when pygments ships a fix.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec .venv/bin/pip-audit --ignore-vuln CVE-2026-4539 "$@"
