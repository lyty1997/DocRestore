#!/usr/bin/env bash
# Run the project's code-quality gate outside Git commit hooks.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

for candidate in \
  "$ROOT/.venv/bin" \
  "$ROOT/venv/bin" \
  "/home/lyty/work/ai/env/anaconda3/envs/docrestore/bin" \
  "/usr/local/bin/typos"
do
  if [ -d "$candidate" ]; then
    PATH="$candidate:$PATH"
  fi
done

fail=0

run_required() {
  echo
  echo "==> $*"
  "$@"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "FAILED: $* (exit $rc)" >&2
    fail=1
  fi
}

require_tool() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "MISSING: $1" >&2
    fail=1
    return 1
  fi
  return 0
}

if require_tool mypy; then
  run_required mypy --strict
fi

if require_tool ruff; then
  run_required ruff check backend tests scripts
fi

if require_tool typos; then
  run_required typos backend tests scripts frontend docs AGENTS.md README.md README.en.md pyproject.toml
fi

if [ -f frontend/package.json ]; then
  if require_tool npm; then
    run_required npm --prefix frontend run typecheck
    run_required npm --prefix frontend run lint
  fi
fi

if require_tool pytest; then
  run_required pytest --tb=short
fi

exit "$fail"
