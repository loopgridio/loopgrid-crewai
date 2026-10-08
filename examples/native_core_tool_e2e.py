"""Real CrewAI 1.15.25 -> LoopGrid Core consequential-tool E2E.

REQUIRES: an independently running LoopGrid Core 0.8.1-design-partner,
crewai==1.15.25, loopgrid==0.8.0, the local loopgrid-crewai RC2.
The action is a sandbox-only local ledger write, NOT a real refund.
No provider API key, external payment, or model endpoint is required.

Run from the extracted RC2 project root:
    python examples/native_core_tool_e2e.py

This is a VALIDATION PROBE. Do not consider it passed until the final
Core evidence assertions and independent verification all pass.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any

from crewai import Agent, BaseLLM, Crew, Process, Task
from crewai.events.types.llm_events import LLMCallType
from crewai.llms.base_llm import llm_call_context
from crewai.tools import tool
from loopgrid import LoopGrid
from loopgrid_crewai import LoopGridCrewAI

BASE = os.getenv("LOOPGRID_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def get_json(path: str) -> dict[str, Any]:
    with urllib.request.urlopen(BASE + path, timeout=15) as response:
        return json.load(response)


def post_json(path: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


class DeterministicToolLLM(BaseLLM):
    """Produce a real CrewAI ReAct tool request and native LLM events."""

    def __init__(self) -> None:
        super().__init__(model="crewai-scripted-core-e2e", temperature=0)
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
                    "Thought: I will use the local sandbox refund tool exactly once.\n"
                    "Action: sandbox_refund\n"
                    'Action Input: {"amount": 25}'
                )
            else:
                response = (
                    "Thought: I have the sandbox tool observation.\n"
                    "Final Answer: The sandbox refund was simulated; no real money moved."
                )
            self._emit_call_completed_event(
                response=response, call_type=LLMCallType.LLM_CALL,
                from_task=from_task, from_agent=from_agent, messages=messages,
            )
            return response

    def supports_function_calling(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 8192


def main() -> None:
    try:
        ready = get_json("/ready")
    except (OSError, urllib.error.HTTPError) as exc:
        raise RuntimeError(
            f"LoopGrid Core is not ready at {BASE}. Start Core first and check {BASE}/ready"
        ) from exc
    if not isinstance(ready, dict):
        raise AssertionError("Unexpected /ready response")

    ws = post_json("/api/v1/workspaces", {
        "name": f"crewai-core-tool-e2e-{uuid.uuid4().hex[:8]}"
    })
    workspace_id = ws["workspace_id"]
    sdk = LoopGrid(base_url=BASE, workspace_id=workspace_id)
    bridge = LoopGridCrewAI(
        client=sdk, workspace_id=workspace_id, agent_id="crewai-sandbox-agent",
        capture_content=False,
    )
    bridge.install()

    # The local ledger is the sandbox's authoritative source for what took place.
    # This is not a real payment or refund service.
    sandbox_ledger: list[dict[str, Any]] = []

    @tool("sandbox_refund")
    def sandbox_refund(amount: int) -> str:
        """Simulate a local sandbox refund; never move real money."""
        entry = {
            "reference": f"local-sandbox-{uuid.uuid4().hex[:12]}",
            "status": "succeeded",
            "amount": amount,
            "currency": "USD",
            "sandbox": True,
            "real_money_moved": False,
        }
        sandbox_ledger.append(entry)
        return json.dumps(entry, sort_keys=True)

    decision = bridge.start_decision(
        decision_type="sandbox_refund",
        agent={"id": "crewai-sandbox-agent", "version": "e2e-v1"},
        authority={"acting_for": "LoopGrid sandbox E2E", "scope": ["refund:create"], "limit_usd": 100},
        model={"provider": "scripted", "name": "crewai-scripted-core-e2e"},
        context={"prompt_version": "crewai-core-tool-e2e-v1"},
        proposed_action={"tool": "sandbox_refund", "amount": 25, "currency": "USD"},
        policy={"policy_id": "sandbox-refund-policy", "version": "1", "decision": "auto_allowed"},
        metadata={"sandbox": True, "real_money_moved": False},
    )
    decision_id = decision["decision_id"]

    llm = DeterministicToolLLM()
    agent = Agent(
        role="Sandbox refund agent",
        goal="Call sandbox_refund once and report that it was only a simulation",
        backstory="Local-only integration test agent without payment-system access.",
        llm=llm,
        tools=[sandbox_refund],
        allow_delegation=False,
        max_iter=3,
        verbose=False,
        cache=False,
    )
    task = Task(
        description="Call sandbox_refund exactly once with amount 25 and report no money moved.",
        expected_output="One sentence confirming this is a sandbox simulation",
        agent=agent,
    )
    crew = Crew(
        agents=[agent], tasks=[task], process=Process.sequential,
        verbose=False, cache=False,
    )

    try:
        with bridge.decision_context(decision_id):
            result = crew.kickoff()
        bridge.flush()
        bridge.assert_healthy()

        assert len(sandbox_ledger) == 1, (
            "Expected exactly one REAL local sandbox tool execution, "
            f"got {len(sandbox_ledger)}"
        )
        observed = sandbox_ledger[0]
        assert observed["amount"] == 25
        assert observed["sandbox"] is True and observed["real_money_moved"] is False
        assert llm.calls >= 2, "CrewAI did not complete the tool-and-response cycle"

        # Application-supplied downstream fact, based on the local ledger —
        # NEVER inferred from a model-generated answer or CrewAI success status.
        bridge.record_outcome(
            decision_id,
            {
                "status": observed["status"],
                "amount": observed["amount"],
                "currency": observed["currency"],
                "sandbox": observed["sandbox"],
                "real_money_moved": observed["real_money_moved"],
                "external_reference": observed["reference"],
            },
            observer="local-sandbox-ledger",
        )
        bridge.assert_healthy()

        detail = sdk.get_decision(decision_id)
        types = [event.get("event_type") for event in detail.get("events", [])]
        summary = detail.get("summary", {})
        coverage = detail.get("coverage", {})
        verification = detail.get("verification", {})
        lifecycle = summary.get("lifecycle", {})
        state = lifecycle.get("state") if isinstance(lifecycle, dict) else lifecycle

        # Consequential action path. There is intentionally no human approval
        # on this auto_allowed policy; approval belongs to a separate E2E.
        expected = [
            "decision_created", "policy_evaluated", "model_completed",
            "tool_requested", "tool_executed", "model_completed",
            "outcome_observed",
        ]
        position = 0
        for kind in types:
            if position < len(expected) and kind == expected[position]:
                position += 1
        assert position == len(expected), f"Required event order missing: {types}"
        assert types.count("tool_requested") == 1, types
        assert types.count("tool_executed") == 1, types
        assert types.count("outcome_observed") == 1, types
        assert "human_approved" not in types and "human_rejected" not in types, types
        assert state == "evidence_complete", detail
        assert coverage.get("score") == 100 and coverage.get("complete") is True, detail
        assert verification.get("valid") is True, detail
        assert verification.get("failures", []) == [], detail

        print(json.dumps({
            "coreVersion": ready.get("version"),
            "workspaceId": workspace_id,
            "decisionId": decision_id,
            "crewaiLLMCalls": llm.calls,
            "actualSandboxToolExecutions": len(sandbox_ledger),
            "actualSandboxAmount": observed["amount"],
            "toolReference": observed["reference"],
            "crewAnswer": str(result),
            "lifecycle": state,
            "coverageScore": coverage.get("score"),
            "coverageComplete": coverage.get("complete"),
            "verifyValid": verification.get("valid"),
            "verificationFailures": verification.get("failures", []),
            "eventTypes": types,
        }, indent=2))
        print("PASS: Real CrewAI sandbox tool -> signed LoopGrid Core evidence complete and verified")
    finally:
        bridge.shutdown()


if __name__ == "__main__":
    main()
