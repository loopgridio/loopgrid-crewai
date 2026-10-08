"""CrewAI 1.15.25 native human rejection -> signed LoopGrid Core evidence.

This test explicitly records a *deterministic application review fixture*; it does
NOT claim production authentication. CrewAI's genuine before-tool hook must deny
execution. No external payment provider, API credentials, or real refunds.

Requirements: running LoopGrid Core 0.8.1-design-partner (default 127.0.0.1:8000).
From your existing RC2 directory, with crewai-spike active:
    python .\\examples\\native_core_human_rejection_e2e.py
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
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


class DeterministicToolLLM(BaseLLM):
    """A deterministic LLM fixture that emits genuine CrewAI model events."""

    def __init__(self) -> None:
        super().__init__(model="crewai-scripted-rejection-e2e", temperature=0)
        self.calls = 0

    def call(
        self,
        messages: Any,
        tools: Any = None,
        callbacks: Any = None,
        available_functions: Any = None,
        **kwargs: Any,
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
                    "Thought: The application denied the request. Do not retry.\n"
                    "Final Answer: The sandbox refund was rejected; no tool action took place."
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
    assert ready.get("ready") is True, f"LoopGrid Core is not ready: {ready}"
    assert ready.get("version") == "0.8.1-design-partner", ready

    workspace = post_json("/api/v1/workspaces", {
        "name": f"crewai-human-rejection-e2e-{uuid.uuid4().hex[:8]}",
    })
    workspace_id = workspace["workspace_id"]
    client = LoopGrid(base_url=BASE, workspace_id=workspace_id)
    bridge = LoopGridCrewAI(
        client=client, workspace_id=workspace_id,
        agent_id="crewai-rejection-test-agent", capture_content=False,
    )
    bridge.install()

    sandbox_ledger: list[dict[str, Any]] = []
    recorded_reviews: list[dict[str, Any]] = []

    # APPLICATION fixture only; production apps must authenticate this reviewer.
    application_review = {
        "reviewer": "crewai-e2e-reviewer.local",
        "approved": False,
        "source": "deterministic-test-fixture",
    }

    @tool("sandbox_refund")
    def sandbox_refund(amount: int) -> str:
        """Simulate a refund in memory without moving any real funds."""
        # Record attempted entry so even CrewAI swallowing this exception cannot
        # make a mistakenly executed tool look like a successful denial.
        sandbox_ledger.append({"amount": amount, "unexpected_execution": True})
        raise AssertionError("SECURITY FAILURE: rejected sandbox tool was executed")

    decision = bridge.start_decision(
        decision_type="sandbox_refund",
        agent={"id": "crewai-rejection-test-agent", "version": "rejection-e2e-v1"},
        authority={
            "acting_for": "LoopGrid local E2E sandbox",
            "scope": ["refund:create"], "limit_usd": 100,
        },
        model={"provider": "scripted", "name": "crewai-scripted-rejection-e2e"},
        context={"prompt_version": "crewai-rejection-e2e-v1"},
        proposed_action={"tool": "sandbox_refund", "amount": 25, "currency": "USD"},
        policy={
            "policy_id": "crewai-local-human-review-policy", "version": "1",
            "decision": "human_approval_required",
        },
        metadata={"sandbox": True, "real_money_moved": False},
    )
    decision_id = decision["decision_id"]

    def application_rejection_gate(ctx: Any) -> bool | None:
        """CrewAI's native before-tool hook; explicitly reject before execution."""
        # Any different tool, repeated call, or altered amount must be denied.
        if ctx.tool_name != "sandbox_refund":
            return False
        if recorded_reviews or ctx.tool_input.get("amount") != 25:
            return False
        assert application_review["approved"] is False
        assert application_review["reviewer"]
        try:
            bridge.record_human_review(
                decision_id,
                reviewer=application_review["reviewer"],
                approved=False,
                reason="Explicit rejection by local deterministic reviewer fixture",
            )
        except Exception:
            # Fail closed: rejection evidence failure must not enable the tool.
            return False
        recorded_reviews.append(dict(application_review))
        return False  # CrewAI must NOT run sandbox_refund.

    # Clear only in this one-shot isolated test process, never in a production app.
    clear_before_tool_call_hooks()
    register_before_tool_call_hook(application_rejection_gate)

    llm = DeterministicToolLLM()
    agent = Agent(
        role="Sandbox rejection-test agent",
        goal="Request a sandbox refund once and respect human rejection",
        backstory="Local sandbox agent subject to application-owned approval",
        llm=llm, tools=[sandbox_refund], allow_delegation=False,
        max_iter=3, verbose=False, cache=False,
    )
    task = Task(
        description="Request sandbox_refund once with amount 25. Stop if rejected.",
        expected_output="Confirm no refund happened after rejection.",
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

        # Neither a model response nor a rejected tool proves a business outcome.
        # Intentionally do NOT call record_outcome() on this rejected path.
        assert len(recorded_reviews) == 1, (
            f"Expected exactly one explicit human rejection: {recorded_reviews}"
        )
        assert recorded_reviews[0]["approved"] is False
        assert recorded_reviews[0]["source"] == "deterministic-test-fixture"
        assert sandbox_ledger == [], "Rejected sandbox tool should have no ledger entries"
        assert llm.calls >= 1, f"No actual model completion: {llm.calls}"

        detail = client.get_decision(decision_id)
        event_types = [e.get("event_type") for e in detail.get("events", [])]
        assert event_types.count("human_rejected") == 1, event_types
        assert "human_approved" not in event_types, event_types
        assert "tool_requested" not in event_types, event_types
        assert "tool_executed" not in event_types, event_types
        assert "outcome_observed" not in event_types, event_types
        assert "model_completed" in event_types, event_types

        # Required prefix: review may occur only AFTER the policy/model evidence.
        expected_prefix = [
            "decision_created", "policy_evaluated",
            "model_completed", "human_rejected",
        ]
        index = 0
        for kind in event_types:
            if index < len(expected_prefix) and kind == expected_prefix[index]:
                index += 1
        assert index == len(expected_prefix), (
            f"Rejection ordering mismatch: {event_types}"
        )

        summary = detail.get("summary", {})
        coverage = detail.get("coverage", {})
        verify = detail.get("verification", {})
        lifecycle = summary.get("lifecycle", {})
        state = lifecycle.get("state") if isinstance(lifecycle, dict) else lifecycle

        report = {
            "coreVersion": ready.get("version"),
            "workspaceId": workspace_id,
            "decisionId": decision_id,
            "reviewerSource": application_review["source"],
            "reviewer": application_review["reviewer"],
            "humanRejectionsRecorded": len(recorded_reviews),
            "actualSandboxToolExecutions": len(sandbox_ledger),
            "crewaiLLMCalls": llm.calls,
            "crewAnswer": str(crew_answer),
            "lifecycle": state,
            "coverageScore": coverage.get("score"),
            "coverageComplete": coverage.get("complete"),
            "verifyValid": verify.get("valid"),
            "verificationFailures": verify.get("failures", []),
            "eventTypes": event_types,
        }
        print(json.dumps(report, indent=2))
        # Verify cryptographic integrity without manufacturing a successful action.
        assert verify.get("valid") is True and verify.get("failures", []) == [], (
            "The rejection evidence chain was not cryptographically verified: "
            + json.dumps(report, indent=2)
        )
        print(
            "PASS: Native CrewAI human rejection -> no tool execution "
            "-> signed LoopGrid Core evidence verified"
        )
    finally:
        clear_before_tool_call_hooks()
        bridge.shutdown()


if __name__ == "__main__":
    main()
