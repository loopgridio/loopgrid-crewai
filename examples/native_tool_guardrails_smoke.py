"""Native CrewAI 1.15.25 blocked/modified tool boundary test for LoopGrid RC2.

This is a VALIDATION PROBE, not a passing claim. A failed assertion exposes
an integration semantics problem that must be fixed before publication.

No LoopGrid Core, provider credentials, real money, or external side effects.
Run from package root: python .\\examples\\native_tool_guardrails_smoke.py
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from crewai import Agent, BaseLLM, Crew, Process, Task
from crewai.events.event_bus import crewai_event_bus
from crewai.events.types.llm_events import LLMCallType
from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent
from crewai.hooks import clear_before_tool_call_hooks, register_before_tool_call_hook
from crewai.llms.base_llm import llm_call_context
from crewai.tools import tool
from loopgrid_crewai import LoopGridCrewAI


class RecordingClient:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.lock = threading.Lock()

    def add_event(self, decision_id: str, event_type: str,
                  payload: dict[str, Any], **kwargs: Any) -> None:
        with self.lock:
            self.events.append((event_type, payload))


class ScriptedLLM(BaseLLM):
    def __init__(self) -> None:
        super().__init__(model="crewai-guardrails-smoke", temperature=0)
        self.calls = 0

    def call(self, messages: Any, tools: Any = None, callbacks: Any = None,
             available_functions: Any = None, **kwargs: Any) -> str:
        self.calls += 1
        from_task = kwargs.get("from_task")
        from_agent = kwargs.get("from_agent")
        with llm_call_context():
            self._emit_call_started_event(
                messages, tools=tools, callbacks=callbacks,
                available_functions=available_functions,
                from_task=from_task, from_agent=from_agent,
            )
            if self.calls == 1:
                answer = (
                    "Thought: Call sandbox_refund once.\n"
                    "Action: sandbox_refund\n"
                    'Action Input: {"amount": 25}'
                )
            else:
                answer = (
                    "Thought: The tool observation is available.\n"
                    "Final Answer: This was a sandbox-only test, not a real refund."
                )
            self._emit_call_completed_event(
                response=answer, call_type=LLMCallType.LLM_CALL,
                from_task=from_task, from_agent=from_agent, messages=messages,
            )
            return answer

    def supports_function_calling(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 8192


actual_calls: list[int] = []


@tool("sandbox_refund")
def sandbox_refund(amount: int) -> str:
    """Simulate an integer sandbox refund without real money movement."""
    actual_calls.append(amount)
    return json.dumps({"amount": amount, "sandbox": True, "real_money_moved": False})


def hash_args(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True,
                   default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def run_case(mode: str) -> None:
    actual_calls.clear()
    clear_before_tool_call_hooks()  # standalone validation process ONLY
    native: list[dict[str, Any]] = []

    def guard(ctx: Any) -> bool | None:
        if ctx.tool_name != "sandbox_refund":
            return None
        if mode == "denied":
            return False  # CrewAI blocks the tool before execution.
        if mode == "modified":
            ctx.tool_input["amount"] = 30  # must be in-place, never replace dict
        return None

    register_before_tool_call_hook(guard)

    @crewai_event_bus.on(ToolUsageFinishedEvent)
    def inspect_native(source: Any, event: Any) -> None:
        if getattr(event, "tool_name", None) == "sandbox_refund":
            native.append({
                "args": getattr(event, "tool_args", None),
                "output": str(getattr(event, "output", ""))[:150],
                "cached": bool(getattr(event, "from_cache", False)),
            })

    client = RecordingClient()
    bridge = LoopGridCrewAI(client=client, agent_id=f"guardrail-{mode}")
    bridge.install()
    llm = ScriptedLLM()
    agent = Agent(
        role="Guardrails test agent", goal="Use sandbox_refund only for testing",
        backstory="Testing-only agent without permission to move real money.",
        llm=llm, tools=[sandbox_refund], allow_delegation=False,
        max_iter=3, verbose=False, cache=False,
    )
    task = Task(
        description="Call sandbox_refund once with amount 25; report sandbox only.",
        expected_output="Sandbox-only test result", agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential,
                verbose=False, cache=False)
    try:
        with bridge.decision_context(f"native-guard-{mode}"):
            crew.kickoff()
        bridge.assert_healthy()
        kinds = [kind for kind, _ in client.events]
        print(f"[{mode}] actual tool calls: {actual_calls}")
        print(f"[{mode}] native CrewAI finished events: {native}")
        print(f"[{mode}] LoopGrid evidence kinds: {kinds}")
        assert kinds.count("model_completed") >= 1, "Missing native CrewAI model evidence"
        assert "human_approved" not in kinds and "outcome_observed" not in kinds, (
            "Adapter must not invent approval or authoritative external outcome"
        )

        if mode == "denied":
            assert actual_calls == [], "DENIAL FAILED: CrewAI executed a blocked tool"
            assert kinds.count("tool_requested") == 0, (
                "DENIAL FAILED: rejected tool appeared as a surviving request"
            )
            assert kinds.count("tool_executed") == 0, (
                "DENIAL FAILED: rejected tool appeared as an executed action"
            )
            print("PASS denied: no execution and no invented tool-action evidence")

        if mode == "modified":
            assert actual_calls == [30], (
                "MODIFICATION FAILED: CrewAI did not execute changed tool arguments"
            )
            requests = [payload for kind, payload in client.events if kind == "tool_requested"]
            executions = [payload for kind, payload in client.events if kind == "tool_executed"]
            assert len(requests) == 1 and len(executions) == 1, (
                "MODIFICATION FAILED: expected exactly one paired tool action"
            )
            expected = hash_args({"amount": 30})
            recorded = requests[0].get("tool_args_commitment", {}).get("sha256")
            print(f"[{mode}] expected executed-argument SHA256: {expected}")
            print(f"[{mode}] LoopGrid recorded-argument SHA256: {recorded}")
            assert recorded == expected, (
                "MODIFICATION FIDELITY FAILURE: LoopGrid request evidence does not "
                "match the actual CrewAI tool input (amount 30). Save this output; "
                "the adapter must be corrected before release."
            )
            print("PASS modified: LoopGrid evidence matches surviving executed parameters")
    finally:
        clear_before_tool_call_hooks()
        bridge.shutdown()


if __name__ == "__main__":
    run_case("denied")
    run_case("modified")
    print("PASS: native CrewAI guardrail semantics validated")
