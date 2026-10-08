"""Run a REAL CrewAI Crew with a deterministic local-only LLM.

No LoopGrid Core, provider credentials, or remote model calls required.
The model uses CrewAI's native LLM event helpers to emit the same
LLM lifecycle events as standard CrewAI providers. It deliberately tests
listener registration/dispatch/flush, not production model-provider behavior.

Run from a CrewAI 1.15.25 environment: python examples/native_runtime_smoke.py
"""
from __future__ import annotations

from typing import Any

from crewai import Agent, BaseLLM, Crew, Process, Task
from crewai.llms.base_llm import llm_call_context
from crewai.events.types.llm_events import LLMCallType
from loopgrid_crewai import LoopGridCrewAI


class RecordingClient:
    def __init__(self):
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    def add_event(self, decision_id, event_type, payload, **kwargs):
        self.events.append((decision_id, event_type, payload))


class LocalLLM(BaseLLM):
    def __init__(self) -> None:
        super().__init__(model="local-deterministic", temperature=0)

    def call(self, messages, tools=None, callbacks=None, available_functions=None, **kwargs):
        # Custom BaseLLM.call() overrides do NOT emit native LLM lifecycle
        # events automatically. Use CrewAI's own provider event helpers.
        from_task = kwargs.get("from_task")
        from_agent = kwargs.get("from_agent")
        with llm_call_context():
            self._emit_call_started_event(
                messages, tools=tools, callbacks=callbacks,
                available_functions=available_functions,
                from_task=from_task, from_agent=from_agent,
            )
            result = "The sandbox operation is complete. No real action was taken."
            self._emit_call_completed_event(
                response=result, call_type=LLMCallType.LLM_CALL,
                from_task=from_task, from_agent=from_agent, messages=messages,
            )
            return result

    def supports_function_calling(self):
        return False

    def get_context_window_size(self):
        return 8192


client = RecordingClient()
integration = LoopGridCrewAI(client=client, agent_id="sandbox-agent")
integration.install()
agent = Agent(
    role="Sandbox evidence analyst",
    goal="Provide a deterministic safety statement",
    backstory="Only produces deterministic local test outputs.",
    llm=LocalLLM(), allow_delegation=False, max_iter=2, verbose=False,
)
task = Task(
    description="Return the sandbox safety statement.",
    expected_output="One sentence", agent=agent,
)
crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
with integration.decision_context("native-runtime-smoke"):
    output = crew.kickoff()
integration.flush()
integration.assert_healthy()
print("CrewAI output:", output)
print("Framework evidence:", [kind for _, kind, _ in client.events])
assert any(kind == "model_completed" for _, kind, _ in client.events), (
    "No model_completed evidence: inspect CrewAI event emission and listener registration"
)
assert not any(kind.startswith("tool_") for _, kind, _ in client.events)
integration.shutdown()
