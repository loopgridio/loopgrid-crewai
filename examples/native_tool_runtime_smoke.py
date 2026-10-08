"""Native CrewAI 1.15.25 tool-execution smoke test for LoopGrid RC2.

No provider credentials or LoopGrid Core. No real-world side effects.
Place in examples/ and run: python examples/native_tool_runtime_smoke.py

This fixture exercises CrewAI's real Agent/Crew/Tool runtime, not a synthetic
ToolUsageFinishedEvent. A deterministic BaseLLM emits CrewAI-native LLM events.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from crewai import Agent, BaseLLM, Crew, Process, Task
from crewai.events.event_bus import crewai_event_bus
from crewai.events.types.llm_events import LLMCallType
from crewai.events.types.tool_usage_events import (
    ToolUsageErrorEvent,
    ToolUsageFinishedEvent,
    ToolUsageStartedEvent,
)
from crewai.llms.base_llm import llm_call_context
from crewai.tools import tool
from loopgrid_crewai import LoopGridCrewAI


class RecordingClient:
    """Captures the actual calls the LoopGrid adapter makes to the SDK."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []
        self.lock = threading.Lock()

    def add_event(self, decision_id: str, event_type: str, payload: dict[str, Any], **kwargs: Any) -> None:
        with self.lock:
            self.events.append((decision_id, event_type, payload))


class DeterministicToolLLM(BaseLLM):
    """One ReAct tool request followed by a final answer, without provider calls."""

    def __init__(self) -> None:
        super().__init__(model="crewai-local-tool-smoke", temperature=0)
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
                response = (
                    "Thought: I must run the sandbox_refund tool exactly once.\n"
                    "Action: sandbox_refund\n"
                    'Action Input: {"amount": 25}'
                )
            else:
                response = (
                    "Thought: I have the sandbox tool observation.\n"
                    "Final Answer: The sandbox refund was simulated. No money moved."
                )
            self._emit_call_completed_event(
                response=response, call_type=LLMCallType.LLM_CALL,
                from_task=from_task, from_agent=from_agent, messages=messages,
            )
            return response

    def supports_function_calling(self) -> bool:
        # Force CrewAI's genuine ReAct text-tool execution path.
        return False

    def get_context_window_size(self) -> int:
        return 8192


actual_tool_calls: list[int] = []


@tool("sandbox_refund")
def sandbox_refund(amount: int) -> str:
    """Simulate a sandbox-only refund of an integer amount; never move money."""
    actual_tool_calls.append(amount)
    return json.dumps({"status": "sandbox_only", "amount": amount, "real_money_moved": False})


native_events: list[tuple[str, str, bool]] = []


@crewai_event_bus.on(ToolUsageStartedEvent)
def observe_started(source: Any, event: Any) -> None:
    native_events.append(("started", str(getattr(event, "tool_name", "")), False))


@crewai_event_bus.on(ToolUsageFinishedEvent)
def observe_finished(source: Any, event: Any) -> None:
    native_events.append(("finished", str(getattr(event, "tool_name", "")),
                          bool(getattr(event, "from_cache", False))))


@crewai_event_bus.on(ToolUsageErrorEvent)
def observe_failed(source: Any, event: Any) -> None:
    native_events.append(("error", str(getattr(event, "tool_name", "")), False))


def main() -> None:
    client = RecordingClient()
    bridge = LoopGridCrewAI(client=client, agent_id="sandbox-agent", capture_content=False)
    bridge.install()
    llm = DeterministicToolLLM()
    agent = Agent(
        role="Sandbox refund agent",
        goal="Run sandbox_refund once and report its simulated result",
        backstory="A deterministic testing-only agent with no real-world permissions.",
        llm=llm,
        tools=[sandbox_refund],
        allow_delegation=False,
        max_iter=3,
        verbose=False,
        cache=False,
    )
    task = Task(
        description="Call sandbox_refund exactly once with amount 25, then report no money moved.",
        expected_output="A brief statement that a sandbox-only tool ran",
        agent=agent,
    )
    crew = Crew(
        agents=[agent], tasks=[task], process=Process.sequential,
        verbose=False, cache=False,
    )
    try:
        with bridge.decision_context("native-tool-runtime-smoke"):
            result = crew.kickoff()
        bridge.flush()
        bridge.assert_healthy()

        kinds = [kind for _, kind, _ in client.events]
        print("CrewAI answer:", str(result))
        print("Actual sandbox tool calls:", actual_tool_calls)
        print("Native CrewAI tool event summaries:", native_events)
        print("LoopGrid evidence kinds:", kinds)
        print("LLM calls:", llm.calls)

        assert actual_tool_calls == [25], (
            "CrewAI must execute the real sandbox tool exactly once (amount 25)."
        )
        assert any(kind == "model_completed" for kind in kinds), (
            "CrewAI native LLM completion evidence is missing."
        )
        assert kinds.count("tool_requested") == 1, (
            "LoopGrid must record exactly one surviving tool request."
        )
        assert kinds.count("tool_executed") == 1, (
            "LoopGrid must record exactly one real tool execution."
        )
        assert kinds.index("tool_requested") < kinds.index("tool_executed"), (
            "Tool request must precede tool execution in evidence."
        )
        assert any(kind == "finished" and name == "sandbox_refund" and not cached
                   for kind, name, cached in native_events), (
            "Missing uncached CrewAI-native ToolUsageFinishedEvent."
        )
        assert "outcome_observed" not in kinds and "human_approved" not in kinds, (
            "The framework must not invent a business outcome or human approval."
        )
        assert all("tool_args" not in payload and "output" not in payload
                   for _, _, payload in client.events), (
            "Raw tool content must not be captured by default."
        )
        print("PASS: Actual CrewAI sandbox tool execution is reflected in LoopGrid evidence.")
    finally:
        bridge.shutdown()


if __name__ == "__main__":
    main()
