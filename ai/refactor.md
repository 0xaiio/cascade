# Iterative Refactor Log

## Baseline

- Goal: improve maintainability without changing user-visible behavior, API wire shapes, database semantics, environment variables, or download behavior.
- Baseline verification before refactoring:
  - `python -m pytest backend\tests -q` -> 75 passed
  - `cd frontend && npm test` -> 28 passed
  - `cd frontend && npm run build` -> passed
- Method: one focused refactor per iteration, targeted/full verification, then a small commit.

## Iteration 1 - Centralize Resolution Fallback Policy

- Problem: fallback reason constants and response-message branching were split across job execution and API serialization, making future fallback changes easy to duplicate incorrectly.
- Reason: fallback policy is domain logic, not route wiring; centralizing it makes behavior reviewable without touching task execution.
- Change: added an internal fallback policy module for reason constants and `ResolutionFallback` response construction; `JobManager` records reasons and `main.py` delegates response construction.
- Verification: `python -m pytest backend\tests -q` -> 75 passed.
- Functional invariance: no API fields, messages, database columns, or download behavior changed.
