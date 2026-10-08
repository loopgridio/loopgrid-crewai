"""Conservative real-Core E2E for actual CrewAI Agent execution.

Requires CrewAI 1.15.25, LoopGrid Core 0.8.1-design-partner at localhost:8000.
This fixture uses real native Crew kickoff + local-only BaseLLM without an external tool;
therefore it must NOT reach `evidence_complete` for a consequential action and deliberately
reports the missing evidence. Do not mistake the successful model-only test for release E2E.
Run `native_runtime_smoke.py` first.
"""
from __future__ import annotations
import json
import os
import urllib.request
import uuid
from crewai import Agent, BaseLLM, Crew, Process, Task
from loopgrid import LoopGrid
from loopgrid_crewai import LoopGridCrewAI

BASE = os.getenv("LOOPGRID_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def post(path, obj):
    request = urllib.request.Request(BASE+path, data=json.dumps(obj).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=15) as r:
        return json.load(r)


class SandboxLLM(BaseLLM):
    def __init__(self): super().__init__(model="crewai-scripted", temperature=0)
    def call(self, messages, tools=None, callbacks=None, available_functions=None, **kwargs):
        return "Sandbox evaluation completed; no external tool was used."
    def supports_function_calling(self): return False
    def get_context_window_size(self): return 8192


workspace = post("/api/v1/workspaces", {"name": f"crewai-e2e-{uuid.uuid4().hex[:8]}"})
client = LoopGrid(base_url=BASE, workspace_id=workspace["workspace_id"])
integration = LoopGridCrewAI(client=client, workspace_id=workspace["workspace_id"])
integration.install()
decision = integration.start_decision(
    decision_type="sandbox_assessment", agent={"id":"crewai-analyst", "version":"e2e"},
    authority={"acting_for":"LoopGrid E2E", "scope":["assess:read"]},
    model={"provider":"scripted", "name":"crewai-scripted"},
    context={"prompt_version":"crewai-e2e-v1"},
    proposed_action={"tool":"none", "sandbox":True},
    policy={"policy_id":"read-only-policy", "version":"1", "decision":"auto_allowed"},
    metadata={"sandbox":True, "real_money_moved":False},
)
agent = Agent(role="Analyst", goal="Return a sandbox statement", backstory="Read only", llm=SandboxLLM(), allow_delegation=False, max_iter=2)
task = Task(description="Return the sandbox assessment.", expected_output="A short statement", agent=agent)
crew = Crew(agents=[agent], tasks=[task], process=Process.sequential)
with integration.decision_context(decision["decision_id"]):
    crew.kickoff()
integration.flush()
integration.assert_healthy()
detail = client.get_decision(decision["decision_id"])
print(json.dumps({
    "workspaceId": workspace["workspace_id"], "decisionId": decision["decision_id"],
    "eventTypes": [e.get("event_type") for e in detail.get("events",[])],
    "gate": "MODEL-ONLY PROBE; NOT CONSEQUENTIAL-TOOL CORE E2E",
}, indent=2))
assert "model_completed" in [e.get("event_type") for e in detail.get("events",[])]
integration.shutdown()
