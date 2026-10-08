"""Real CrewAI 1.15.25 native pre-tool approval -> LoopGrid Core E2E.

This is a *deterministic local test reviewer fixture*, NOT production authentication.
The application owns identity/approval and CrewAI owns actual tool execution.
No API keys, payment rails, or actual refunds are involved.

Requirements:
- CrewAI v1.15.25, LoopGrid SDK v0.8.0, loopgrid-crewai RC2 installed
- Running LoopGrid Core v0.8.1-design-partner (default http://127.0.0.1:8000)
Run from the extracted RC2 directory:
    python .\\examples\\native_core_human_approval_e2e.py
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
from typing import Any

from crewai import Agent, BaseLLM, Crew, Process, Task
from crewai.events.types.llm_events import LLMCallType
from crewai.hooks import clear_before_tool_call_hooks, register_before_tool_call_hook
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
    """Emit *real native* CrewAI LLM completion events around a ReAct tool call."""

    def __init__(self) -> None:
        super().__init__(model="crewai-scripted-approval-e2e", temperature=0)
        self.calls = 0

    def call(
        self, messages: Any, tools: Any = None, callbacks: Any = None,
        available_functions: Any = None, **kwargs: Any,
    ) -> str:
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
                    "Thought: Request the sandbox refund exactly once.\n"
                    "Action: sandbox_refund\n"
                    'Action Input: {"amount": 25}'
                )
            else:
                response = (
                    "Thought: Sandbox execution returned an observation.\n"
                    "Final Answer: The approved sandbox refund was simulated; no real money moved."
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
    ready = get_json("/ready")
    assert ready.get("ready") is True, f"Core not ready: {ready}"
    assert ready.get("version") == "0.8.1-design-partner", ready

    workspace = post_json("/api/v1/workspaces", {
        "name": f"crewai-human-approval-e2e-{uuid.uuid4().hex[:8]}"
    })
    workspace_id = workspace["workspace_id"]
    client = LoopGrid(base_url=BASE, workspace_id=workspace_id)
    bridge = LoopGridCrewAI(
        client=client, workspace_id=workspace_id,
        agent_id="crewai-approval-test-agent", capture_content=False,
    )
    bridge.install()

    sandbox_ledger: list[dict[str, Any]] = []
    reviewed: list[dict[str, Any]] = []

    # Trusted APPLICATION fixture: in production, populate this ONLY from an
    # authenticated reviewer session and an actual review decision.
    application_review = {
        "reviewer": "crewai-e2e-reviewer.local",
        "approved": True,
        "source": "deterministic-test-fixture",
    }

    @tool("sandbox_refund")
    def sandbox_refund(amount: int) -> str:
        """Record a local fake refund only; no external payment systems."""
        assert len(reviewed) == 1 and reviewed[0]["approved"] is True, (
            "FAIL-CLOSED: sandbox tool would execute without confirmed review"
        )
        entry = {
            "reference": f"local-sandbox-{uuid.uuid4().hex[:12]}",
            "status": "succeeded", "amount": amount, "currency": "USD",
            "sandbox": True, "real_money_moved": False,
        }
        sandbox_ledger.append(entry)
        return json.dumps(entry, sort_keys=True)

    decision = bridge.start_decision(
        decision_type="sandbox_refund",
        agent={"id": "crewai-approval-test-agent", "version": "approval-e2e-v1"},
        authority={
            "acting_for": "LoopGrid local E2E sandbox", "scope": ["refund:create"],
            "limit_usd": 100,
        },
        model={"provider": "scripted", "name": "crewai-scripted-approval-e2e"},
        context={"prompt_version": "crewai-approval-e2e-v1"},
        proposed_action={"tool": "sandbox_refund", "amount": 25, "currency": "USD"},
        policy={
            "policy_id": "crewai-local-approval-policy", "version": "1",
            "decision": "human_approval_required",
        },
        metadata={"sandbox": True, "real_money_moved": False},
    )
    decision_id = decision["decision_id"]

    def application_approval_gate(ctx: Any) -> bool | None:
        """A real CrewAI before-tool hook runs before the tool body.

        We fail closed on unapproved/duplicate/unexpected calls and on recording
        failures. This fixture does not infer approval from execution success.
        """
        if ctx.tool_name != "sandbox_refund":
            return False
        if reviewed or ctx.tool_input.get("amount") != 25:
            return False
        if not application_review["approved"] or not application_review["reviewer"]:
            return False
        try:
            bridge.record_human_review(
                decision_id,
                reviewer=application_review["reviewer"],
                approved=True,
                reason="Explicit local test-fixture approval for amount 25",
            )
        except Exception:
            # If evidence transport fails, deny the execution rather than
            # allowing an unaudited consequential tool action.
            return False
        reviewed.append(dict(application_review))
        return None  # CrewAI may continue with the actual tool execution.

    # This is an isolated test process. Production apps should NOT globally
    # clear hooks that may have been installed by other components.
    clear_before_tool_call_hooks()
    register_before_tool_call_hook(application_approval_gate)

    llm = DeterministicToolLLM()
    agent = Agent(
        role="Sandbox approval-test agent",
        goal="Request sandbox_refund once and report that no real money moved",
        backstory="Local sandbox only, with application-controlled review",
        llm=llm, tools=[sandbox_refund], allow_delegation=False,
        max_iter=3, verbose=False, cache=False,
    )
    task = Task(
        description="Request sandbox_refund exactly once with amount 25; report sandbox only.",
        expected_output="One sentence confirming that the refund was simulated.",
        agent=agent,
    )
    crew = Crew(
        agents=[agent], tasks=[task], process=Process.sequential,
        verbose=False, cache=False,
    )

    try:
        with bridge.decision_context(decision_id):
            crew_answer = crew.kickoff()
        bridge.flush()
        bridge.assert_healthy()

        assert len(reviewed) == 1, f"Expected one explicit approval: {reviewed}"
        assert reviewed[0]["source"] == "deterministic-test-fixture"
        assert len(sandbox_ledger) == 1, (
            f"Expected exactly one sandbox execution: {sandbox_ledger}"
        )
        observed = sandbox_ledger[0]
        assert observed["amount"] == 25 and observed["real_money_moved"] is False
        assert llm.calls >= 2, f"Expected tool-and-answer agent cycle: {llm.calls}"

        # Only the application ledger—not the LLM's answer—supplies outcome.
        bridge.record_outcome(
            decision_id,
            {
                "status": observed["status"], "amount": observed["amount"],
                "currency": observed["currency"],
                "sandbox": observed["sandbox"],
                "real_money_moved": observed["real_money_moved"],
                "external_reference": observed["reference"],
            },
            observer="local-sandbox-ledger",
        )
        bridge.assert_healthy()

        detail = client.get_decision(decision_id)
        types = [event.get("event_type") for event in detail.get("events", [])]
        expected = [
            "decision_created", "policy_evaluated", "model_completed",
            "human_approved", "tool_requested", "tool_executed",
            "model_completed", "outcome_observed",
        ]
        index = 0
        for kind in types:
            if index < len(expected) and kind == expected[index]:
                index += 1
        assert index == len(expected), f"Human-review event ordering mismatch: {types}"
        assert types.count("human_approved") == 1, types
        assert "human_rejected" not in types, types
        assert types.count("tool_requested") == 1, types
        assert types.count("tool_executed") == 1, types
        assert types.count("outcome_observed") == 1, types

        summary = detail.get("summary", {})
        coverage = detail.get("coverage", {})
        verify = detail.get("verification", {})
        lifecycle = summary.get("lifecycle", {})
        state = lifecycle.get("state") if isinstance(lifecycle, dict) else lifecycle
        assert state == "evidence_complete", detail
        assert coverage.get("score") == 100 and coverage.get("complete") is True, detail
        assert verify.get("valid") is True and verify.get("failures", []) == [], detail

        print(json.dumps({
            "coreVersion": ready.get("version"),
            "workspaceId": workspace_id,
            "decisionId": decision_id,
            "reviewerSource": application_review["source"],
            "reviewer": application_review["reviewer"],
            "reviewRecorded": len(reviewed),
            "actualSandboxToolExecutions": len(sandbox_ledger),
            "actualSandboxAmount": observed["amount"],
            "realMoneyMoved": observed["real_money_moved"],
            "crewaiLLMCalls": llm.calls,
            "crewAnswer": str(crew_answer),
            "lifecycle": state,
            "coverageScore": coverage.get("score"),
            "coverageComplete": coverage.get("complete"),
            "verifyValid": verify.get("valid"),
            "verificationFailures": verify.get("failures", []),
            "eventTypes": types,
        }, indent=2))
        print("PASS: CrewAI native approval hook -> one sandbox tool -> Core signed verified evidence")
    finally:
        clear_before_tool_call_hooks()
        bridge.shutdown()


if __name__ == "__main__":
    main()
