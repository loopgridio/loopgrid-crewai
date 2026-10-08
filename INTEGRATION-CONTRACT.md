# CrewAI → LoopGrid evidence contract (RC4)

- Application creates the decision with real delegated authority; may record real application policy via `policy_evaluated`.
- CrewAI `LLMCallCompletedEvent` is the source of `model_completed`, not a string inferred from CrewOutput.
- CrewAI `ToolUsageFinishedEvent` is a post-fact source for two ordered evidence records: `tool_requested`, `tool_executed`. Exclude cached results; guardrail-blocked results must be excluded. Native denial/modify E2Es are mandatory before release.
- `AgentExecutionCompletedEvent`, `CrewKickoffCompletedEvent`, task completion, and a successful model answer are NOT evidence of a downstream business outcome.
- Only the authenticated application can record a real reviewer, policy provenance and observed downstream outcome.
- SHA-256 commitments default for LLM/tool content. Do not make synthetic tool calls, approvals, or outcomes.
- Tool event occurrence identity is per scope, process-local and isolated with a random per-run identifier; no stable external tool ID is claimed. The same event object is deduplicated within the bounded 2,048-entry active-scope window; a distinct identical real invocation is not collapsed. Replayed events after eviction or restart may reappear.
- No implicit interception or replacement of third-party CrewAI listeners or hooks. Async/detached thread context propagation must be validated.
- Native framework listeners and global hooks can coexist; this integration never registers a blocking hook.
- Core release gate: `evidence_complete`, applicable coverage 100%, `verification.valid = true`, zero failures, and correct event sequence for both normal tool and authenticated human approval cases.
