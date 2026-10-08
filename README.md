# LoopGrid for CrewAI

Record CrewAI agent model completions, tool executions, policy decisions, and human reviews as cryptographically verifiable LoopGrid evidence. Connects to your LoopGrid Core service; the bridge observes CrewAI events without executing business actions.

## Install

```bash
pip install loopgrid-crewai
```

The integration has passed native CrewAI and signed LoopGrid Core end-to-end tests for the versions documented in [VALIDATION.md](VALIDATION.md).

## What this does

CrewAI publishes lifecycle events using `BaseEventListener` and the global event bus. This adapter observes:

- `LLMCallCompletedEvent` → `model_completed` with CrewAI `call_id`, model, agent/task provenance, response commitment
- `ToolUsageFinishedEvent` when uncached/unblocked → `tool_requested`, then `tool_executed` as **paired, post-fact evidence**
- policy and delegated authority → only explicit application facts supplied to `start_decision()` / `record_policy()`
- application-supplied human review → only explicit `record_human_review()` call
- real-world outcome → only explicit `record_outcome()` call after authoritative downstream observation

It does not execute tools, gate actions, infer success from a tool response, or infer financial/physical effects.

**Tool event semantics.** CrewAI's pre/post tool hooks can run for a blocked action, and `ToolUsageStartedEvent` alone cannot prove execution. The adapter deliberately records tool evidence when a completed, uncached CrewAI tool event is observed. Tool start and completion times remain native event facts, not invented ordering claims. A cached result is not treated as a new execution. Denied and modified hooks were validated in real CrewAI runtime tests on RC4. General guarantees across every CrewAI tool implementation are not claimed.

## Develop or test from source

Python **3.10–3.13** (CrewAI currently excludes Python 3.14):

```powershell
python -m venv ..\crewai-spike
..\crewai-spike\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pip show crewai loopgrid
python -m pytest -v
python -m compileall -q src examples tests
python .\examples\native_runtime_smoke.py
```

Target versions: `crewai==1.15.25`, `loopgrid==0.8.0`, Core `0.8.1-design-partner`.

## Connect to a CrewAI Crew

```python
from crewai import Agent, Crew, Task, Process
from loopgrid_crewai import LoopGridCrewAI

bridge = LoopGridCrewAI(
    base_url="http://127.0.0.1:8000", workspace_id="default",
    agent_id="billing-crew",
)
bridge.install()  # register CrewAI native BaseEventListener once

decision = bridge.start_decision(
    decision_type="customer_refund",
    agent={"id":"billing-crew", "version":"1"},
    authority={"acting_for":"Example Store", "scope":["refund:create"], "limit_usd":100},
    model={"provider":"openai", "name":"configured-model"},
    context={"prompt_version":"refund-v1"},
    proposed_action={"tool":"refund.create", "amount":25},
    policy={"policy_id":"refund-policy", "version":"1", "decision":"auto_allowed"},
    metadata={"sandbox":True, "real_money_moved":False},
)

# Create your actual CrewAI Crew using the application's agents/tasks/tools.
crew = Crew(agents=[...], tasks=[...], process=Process.sequential)
with bridge.decision_context(decision["decision_id"]):
    result = crew.kickoff()
bridge.flush()
bridge.assert_healthy()  # transport success does not imply evidence completeness

# ONLY after an authoritative downstream system actually reports the outcome:
bridge.record_outcome(decision["decision_id"], {"status":"succeeded", "external_reference":"actual-external-id"}, observer="billing-webhook")
```

**Important:** A model-generated statement or CrewOutput alone is NOT an authoritative downstream business outcome. The example above shows the API, not a self-contained runnable Crew. For a runnable real Crew without API credentials see `examples/native_runtime_smoke.py`.

### Human review and guardrails

Use CrewAI's native pre-tool execution hooks to block or modify actions. Supply authenticated reviewer information from your own application; after approval, explicitly invoke:

```python
bridge.record_human_review(decision_id, reviewer="reviewer-id", approved=True, reason="Approved by operator")
```

This drains earlier queued evidence before the explicit review. The tool event listener is **observation-only** and is not an approval system. Denied and modified tool hooks passed Windows native runtime checks. Reviewer authentication remains application-owned.

### Privacy / failure handling

`capture_content=False` by default. Tool arguments and results plus LLM responses are stored as SHA-256 commitments. Explicit application-provided metadata, agent roles, tool names and policy values may be visible; do not put secrets there. Opting into `capture_content=True` exports raw content.

The listener queues events to one FIFO worker, never intentionally blocks execution for network I/O, and retains transport failures for `assert_healthy()` after `flush()`. An error-free Crew execution is **not** proof that evidence was recorded successfully.

### Correlation and limitations

`decision_context` uses Python `contextvars`. It isolates nested and parallel task contexts when propagated. Detached threads without propagated context are **ignored rather than misattributed**. Each invocation has short-lived per-scope ordinal/deduplication state and a random local run ID; an application may reopen the same decision without colliding with prior run keys. State becomes collectible when the context and dispatched handlers finish. Duplicate windows are capped at **2,048 tool objects and 2,048 LLM call identities per active scope**. Replayed events older than the window may not be suppressed. CrewAI tool-completion events do not expose a stable provider tool-call ID, and this integration does **not** claim cross-process exactly-once delivery or deduplication of a newly constructed replayed tool event.

Core only marks a decision `evidence_complete` when its applicable evidence is actually recorded. A `verify.valid=true` result means evidence integrity, not business truth or safety.

## Executable examples

Run inside a disposable virtual environment installed with `python -m pip install -e ".[dev]"`.

| Script | Scope | Needs Core |
|---|---|---|
| `examples/native_runtime_smoke.py` | Real CrewAI local LLM completion | No |
| `examples/native_tool_runtime_smoke.py` | Real sandbox tool start/completion, one execution | No |
| `examples/native_tool_guardrails_smoke.py` | Native denial and modified tool args | No |
| `examples/native_core_tool_e2e.py` | One sandbox action → signed Core evidence | Yes |
| `examples/native_core_human_approval_e2e.py` | Deterministic explicit reviewer → approved action → signed Core evidence | Yes |
| `examples/native_core_human_rejection_e2e.py` | Deterministic explicit reviewer → rejection without action → verified Core evidence | Yes |

The reviewer used by E2E scripts is **a deterministic fixture, not a production-authenticated reviewer**. The application remains responsible for real authentication and authorization. Neither `capture_content=False` nor raw SHA-256 commitments make low-entropy input unrecoverable; avoid recording secrets in explicit metadata, roles, or tool names.

**Listener lifetime:** create one bridge per intended application scope, call `shutdown()` after scoped runs and `flush()` are finished. `shutdown()` cannot be reversed; use a new bridge if required. Active-scoped deduplication is bounded; redacted transport error history retains the most recent 256 entries. CrewAI owns a process-global event bus, so handler registrations may remain as inert weak-owner callbacks until process exit.

**Important limitations:** completion-derived tool evidence is an ordered post-fact pair, not proof of pre-execution interception; no cross-process exactly-once action identity is promised. Multi-agent/hierarchical, deeply concurrent, provider-backed, cached-result, and detached-thread behavior must be checked separately before broad compatibility claims. Successful verification proves record integrity, not correctness of business facts.

## Release gates

See [VALIDATION.md](VALIDATION.md) for the Windows RC4 native, Core, distribution, and clean-wheel test results. GitHub CI and any public PyPI deployment are separately verified at release time.

Apache-2.0.
