# Changelog

## v0.1.0 RC1 — unpublished

Initial native CrewAI event-listener adapter: explicit decision, policy, review and outcome APIs; SHA-256 commitments by default; FIFO transport; basic decision context and offline contract tests.

## v0.1.0 RC2 — unpublished

- Synchronize CrewAI's event dispatch before draining LoopGrid's transport; preserve decision context until callbacks finish.
- Correct local deterministic `BaseLLM` example to emit native completion events.
- Validate native CrewAI tool execution, guardrails and signed Core decision lifecycles on Windows.

## v0.1.0 RC3 — unpublished; validated on Windows on 2026-10-08

- Consolidate six native/Core scripts and harden listener shutdown, error redaction and weak owner references.
- 33/33 unit tests; native and Core end-to-end cases pass in editable and clean-wheel installs on Windows.
- Wheel, sdist, Twine metadata, and dependency checks passed. See `VALIDATION.md` for evidence and limits.

## v0.1.0 RC4 — Windows native/Core/fresh-wheel validated on 2026-10-08

- Replace long-lived, decision-ID-indexed duplicate/ordinal state with per-`decision_context` execution state. After CrewAI callbacks and LoopGrid events flush, state can be garbage-collected rather than retained for the lifetime of the adapter.
- Give each scope a random run identifier, so re-opening the same decision ID does not reuse earlier tool/LLM idempotency keys, including under concurrent execution.
- Bound both tool-object and LLM-call duplicate windows at 2,048 items **per active scope**. Bound redacted error history to 256 entries.
- Add five focused regression tests: garbage collection, reopened decision IDs, concurrent reuse of decision IDs, long-running windows and diagnostic bounds. Offline result: **38/38 passed** in the preparation environment.
- Windows validation: 38/38 tests passed; native model/tool/guardrail scenarios passed; three signed Core E2Es reached coverage 100 with valid verification and no failures; wheel/sdist built and metadata checked; six clean-wheel native/Core tests passed.
- GitHub-ready packaging, CI matrix, release version guard, and gated PyPI Trusted Publishing workflow prepared. GitHub CI and public PyPI publication have not yet been performed.
