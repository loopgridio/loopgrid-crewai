"""Offline contract fakes; NOT a substitute for native CrewAI runtime tests."""
from __future__ import annotations
import importlib
import sys
import types
import pytest


class FakeBus:
    def __init__(self):
        self.handlers = {}

    def flush(self, timeout=30.0):
        return True

    def on(self, cls):
        def deco(fn):
            self.handlers.setdefault(cls, []).append(fn)
            return fn
        return deco

    def emit(self, event):
        for fn in self.handlers.get(type(event), []):
            fn(None, event)


class BaseEventListener:
    def __init__(self):
        self.setup_listeners(FakeBusHolder.bus)


class FakeBusHolder:
    bus = FakeBus()


class LLMCallCompletedEvent:
    def __init__(self, *, call_id, response="done", model="scripted", agent_id="a", task_id="t"):
        self.call_id, self.response, self.model = call_id, response, model
        self.agent_id, self.task_id, self.agent_role = agent_id, task_id, "Researcher"
        self.call_type = "llm_call"


class ToolUsageFinishedEvent:
    def __init__(self, tool_name="sandbox_refund", tool_args=None, *, output="ok", from_cache=False, failure=None, agent_id="a", task_id="t"):
        self.tool_name = tool_name
        self.tool_args = tool_args if tool_args is not None else {"amount": 25}
        self.output, self.from_cache, self.failure = output, from_cache, failure
        self.agent_id, self.task_id, self.agent_role = agent_id, task_id, "Researcher"


class FakeClient:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.counter = 0

    def _append(self, name, *args, **kwargs):
        if self.fail and name == "add_event":
            raise RuntimeError("offline")
        self.calls.append((name, args, kwargs))
        return {"ok": True}

    def record_decision(self, **kwargs):
        self.counter += 1
        self._append("record_decision", **kwargs)
        return {"decision_id": f"dec_{self.counter}"}

    def policy_evaluated(self, *a, **kw): return self._append("policy_evaluated", *a, **kw)
    def human_approved(self, *a, **kw): return self._append("human_approved", *a, **kw)
    def human_rejected(self, *a, **kw): return self._append("human_rejected", *a, **kw)
    def outcome_observed(self, *a, **kw): return self._append("outcome_observed", *a, **kw)
    def add_event(self, *a, **kw): return self._append("add_event", *a, **kw)


@pytest.fixture
def runtime(monkeypatch):
    """Install synthetic module surfaces only while importing our adapter; restore afterward."""
    FakeBusHolder.bus = FakeBus()
    crewai = types.ModuleType("crewai")
    events = types.ModuleType("crewai.events")
    events.BaseEventListener = BaseEventListener
    events.LLMCallCompletedEvent = LLMCallCompletedEvent
    events.ToolUsageFinishedEvent = ToolUsageFinishedEvent
    event_bus_module = types.ModuleType("crewai.events.event_bus")
    event_bus_module.crewai_event_bus = FakeBusHolder.bus
    crewai.events = events
    sdk = types.ModuleType("loopgrid")
    sdk.LoopGrid = FakeClient
    monkeypatch.setitem(sys.modules, "crewai", crewai)
    monkeypatch.setitem(sys.modules, "crewai.events", events)
    monkeypatch.setitem(sys.modules, "crewai.events.event_bus", event_bus_module)
    monkeypatch.setitem(sys.modules, "loopgrid", sdk)
    for name in ("loopgrid_crewai.integration", "loopgrid_crewai"):
        sys.modules.pop(name, None)
    from loopgrid_crewai import LoopGridCrewAI
    client = FakeClient()
    integration = LoopGridCrewAI(client=client, agent_id="crew-agent")
    integration.install()
    yield integration, client, FakeBusHolder.bus
    integration.shutdown()
    for name in ("loopgrid_crewai.integration", "loopgrid_crewai"):
        sys.modules.pop(name, None)
