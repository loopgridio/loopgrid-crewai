"""Native CrewAI event-bus -> LoopGrid decision-evidence bridge.

Strictly observes CrewAI events; never executes/authorizes tools or infers external outcomes.
The application explicitly creates a decision and supplies authorization, policy and outcome.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import queue
import threading
import uuid
import weakref
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from crewai.events.event_bus import crewai_event_bus
from crewai.events import (
    BaseEventListener,
    LLMCallCompletedEvent,
    ToolUsageFinishedEvent,
)
from loopgrid import LoopGrid

CREWAI_TARGET_VERSION = "1.15.25"
LOOPGRID_CREWAI_VERSION = "0.1.0"


def _require(name: str, v: Any) -> None:
    if v is None or v == "" or v == {} or v == []:
        raise ValueError(f"{name} is required")


def _bytes(v: Any) -> bytes:
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")


def _commitment(v: Any) -> dict[str, Any]:
    b = _bytes(v)
    return {"algorithm": "sha256", "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)}


def _key(*parts: Any) -> str:
    return "lg-crewai-" + hashlib.sha256(_bytes(parts)).hexdigest()


@dataclass(frozen=True)
class _Work:
    decision_id: str
    kind: str
    payload: dict[str, Any]
    actor_type: str
    actor_id: str
    idempotency_key: str


@dataclass
class _DecisionScope:
    """Per-kickoff state: released once the scoped CrewAI callbacks are flushed.

    A random run ID separates two kickoffs that reopen the same decision. Tool
    ordinal and duplicate windows must never be shared across parallel scopes.
    """
    decision_id: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ordinal: int = 0
    seen_tools: OrderedDict[int, Any] = field(default_factory=OrderedDict)
    seen_llm: OrderedDict[tuple[str, str, str], None] = field(default_factory=OrderedDict)


class _Transport:
    def __init__(self, owner: "LoopGridCrewAI") -> None:
        self.owner = owner
        self.queue: queue.Queue[_Work | object] = queue.Queue()
        self.stop_item = object()
        self.worker: threading.Thread | None = None
        self.start_lock = threading.Lock()
        self.closed = False

    def enqueue(self, job: _Work) -> None:
        with self.start_lock:
            if self.closed:
                raise RuntimeError("LoopGrid CrewAI transport is closed")
            if self.worker is None:
                self.worker = threading.Thread(target=self._work, name="loopgrid-crewai", daemon=True)
                self.worker.start()
            self.queue.put(job)

    def _work(self) -> None:
        while True:
            job = self.queue.get()
            try:
                if job is self.stop_item:
                    return
                assert isinstance(job, _Work)
                try:
                    self.owner.client.add_event(
                        job.decision_id, job.kind, job.payload,
                        actor_type=job.actor_type, actor_id=job.actor_id,
                        idempotency_key=job.idempotency_key,
                    )
                except Exception as exc:
                    self.owner._error("transport", job.decision_id, exc)
            finally:
                self.queue.task_done()

    def flush(self) -> None:
        self.queue.join()

    def close(self) -> None:
        with self.start_lock:
            if self.closed:
                return
            self.closed = True
            worker = self.worker
            if worker is not None:
                self.queue.put(self.stop_item)
        if worker is not None:
            self.queue.join()
            worker.join(timeout=5)
            if worker.is_alive():
                raise TimeoutError("LoopGrid CrewAI evidence worker did not stop")


class _CrewListener(BaseEventListener):
    """Install once per integration instance; inactive outside a decision scope."""

    def __init__(self, owner: "LoopGridCrewAI") -> None:
        # The process-wide CrewAI bus retains handler functions; avoid retaining
        # user clients, credentials, and the integration owner indefinitely.
        self._owner_ref = weakref.ref(owner)
        super().__init__()

    def setup_listeners(self, crewai_event_bus: Any) -> None:
        @crewai_event_bus.on(LLMCallCompletedEvent)
        def on_llm_complete(source: Any, event: Any) -> None:
            owner = self._owner_ref()
            if owner is not None and owner._active:
                owner._observe_llm(event)

        @crewai_event_bus.on(ToolUsageFinishedEvent)
        def on_tool_finished(source: Any, event: Any) -> None:
            owner = self._owner_ref()
            if owner is not None and owner._active:
                owner._observe_tool_finished(event)


class LoopGridCrewAI:
    """Application-scoped evidence bridge using CrewAI's native event listener API.

    Construct once, call ``install()``, then wrap ``crew.kickoff()`` in
    ``decision_context(decision_id)``. Context is not guessed from a global crew name.
    Unscoped events are deliberately ignored to prevent cross-tenant evidence leakage.

    ToolUsageFinishedEvent is used for the action boundary (rather than pre-tool
    events which can be rejected, modified, or cached). An observed completion
    emits `tool_requested` then `tool_executed` as a paired, post-fact sequence.
    A cached result is not a fresh tool execution and is therefore excluded.
    """

    def __init__(
        self, *, client: LoopGrid | None = None,
        base_url: str = "http://127.0.0.1:8000", api_key: str | None = None,
        workspace_id: str = "default", agent_id: str = "crewai-agent",
        capture_content: bool = False,
    ) -> None:
        _require("agent_id", agent_id)
        self.client = client if client is not None else LoopGrid(
            base_url=base_url, api_key=api_key, workspace_id=workspace_id,
        )
        self.workspace_id = workspace_id
        self.agent_id = agent_id
        self.capture_content = bool(capture_content)
        self._scope: contextvars.ContextVar[_DecisionScope | None] = contextvars.ContextVar(
            f"loopgrid_crewai_scope_{id(self)}", default=None,
        )
        self._errors: list[dict[str, str | None]] = []
        self._lock = threading.RLock()
        self._installed: _CrewListener | None = None
        self._active = True
        self._transport = _Transport(self)

    def install(self) -> None:
        """Register native listener once. Keep the integration instance alive."""
        if not self._active:
            raise RuntimeError("LoopGrid CrewAI integration has been shut down")
        if self._installed is None:
            self._installed = _CrewListener(self)

    @property
    def current_decision_id(self) -> str | None:
        scope = self._scope.get()
        return scope.decision_id if scope is not None else None

    @contextlib.contextmanager
    def decision_context(self, decision_id: str) -> Iterator[None]:
        _require("decision_id", decision_id)
        if not self._active:
            raise RuntimeError("LoopGrid CrewAI integration has been shut down")
        token = self._scope.set(_DecisionScope(decision_id))
        try:
            yield
        finally:
            # CrewAI dispatches sync observers in a thread pool. Wait while the
            # scope remains active; otherwise later callbacks could be discarded.
            try:
                self.flush()
            finally:
                self._scope.reset(token)

    def start_decision(
        self, *, decision_type: str, agent: Mapping[str, Any],
        authority: Mapping[str, Any], model: Mapping[str, Any],
        context: Mapping[str, Any], proposed_action: Mapping[str, Any],
        policy: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        privacy_mode: str = "redacted", idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        _require("decision_type", decision_type)
        _require("agent.id", agent.get("id"))
        _require("authority", authority)
        _require("model.name", model.get("name"))
        _require("context", context)
        _require("proposed_action", proposed_action)
        md = dict(metadata or {})
        md.update({
            "framework": "crewai", "integration": "loopgrid-crewai",
            "integration_version": LOOPGRID_CREWAI_VERSION,
            "crewai_target": CREWAI_TARGET_VERSION,
        })
        result = self.client.record_decision(
            decision_type=decision_type, agent=dict(agent), authority=dict(authority),
            model=dict(model), context=dict(context), proposed_action=dict(proposed_action),
            metadata=md, privacy_mode=privacy_mode, idempotency_key=idempotency_key,
        )
        if policy is not None:
            self.record_policy(result["decision_id"], policy)
        return result

    def record_policy(self, decision_id: str, policy: Mapping[str, Any]) -> Any:
        p = dict(policy)
        if p.get("decision") not in {"auto_allowed", "human_approval_required", "blocked", "block"}:
            raise ValueError("policy.decision must be auto_allowed, human_approval_required, blocked, or block")
        if not (p.get("policy_id") or p.get("version")):
            raise ValueError("policy provenance requires policy_id or version")
        return self.client.policy_evaluated(
            decision_id, p, actor_id=str(p.get("policy_id") or "application-policy")
        )

    def record_human_review(self, decision_id: str, *, reviewer: str, approved: bool, reason: str = "") -> Any:
        _require("reviewer", reviewer)
        self.flush()   # FIFO boundary: LLM completion must precede human review.
        if approved:
            return self.client.human_approved(decision_id, reviewer, reason)
        return self.client.human_rejected(decision_id, reviewer, reason)

    def record_outcome(
        self, decision_id: str, outcome: Mapping[str, Any], *, observer: str = "application"
    ) -> Any:
        _require("outcome", outcome)
        self.flush()   # Queued evidence must arrive before authoritative outcome.
        return self.client.outcome_observed(decision_id, dict(outcome), actor_id=observer)

    def _error(self, stage: str, decision_id: str | None, exc: BaseException) -> None:
        with self._lock:
            self._errors.append({
                "stage": stage, "decision_id": decision_id,
                # Never return raw exception messages: HTTP/client exceptions
                # may include request URLs, tokens, payloads, or customer data.
                "error_type": type(exc).__name__,
            })
            # Diagnostics must not themselves exhaust memory during a
            # prolonged Core outage. Never erase the fact that errors exist.
            if len(self._errors) > 256:
                del self._errors[:-256]

    def assert_healthy(self) -> None:
        with self._lock:
            if self._errors:
                raise RuntimeError(f"LoopGrid CrewAI evidence errors: {self._errors}")

    def flush(self) -> None:
        # Wait for native CrewAI event delivery BEFORE draining our worker.
        # A queue-only flush races CrewAI's async event-handler dispatcher.
        flushed = crewai_event_bus.flush(timeout=30.0)
        if flushed is False:
            raise TimeoutError("CrewAI event handlers did not flush within 30 seconds")
        self._transport.flush()

    def shutdown(self) -> None:
        """Drain queued events and deactivate this owner (not re-installable)."""
        if not self._active:
            return
        # Wait for CrewAI callbacks while scope is still resolvable.
        try:
            self.flush()
        finally:
            self._active = False
            self._transport.close()

    def _next(self, scope: _DecisionScope) -> int:
        with self._lock:
            scope.ordinal += 1
            return scope.ordinal

    def _seen_event(self, scope: _DecisionScope, event: Any) -> bool:
        """Suppress re-delivery of SAME event object; new identical calls remain distinct.

        This is process-local only. ToolUsageFinishedEvent exposes no stable call ID;
        exactly-once recovery after process restart is therefore NOT claimed.
        """
        with self._lock:
            recent = scope.seen_tools
            oid = id(event)
            if oid in recent and recent[oid] is event:
                return True
            recent[oid] = event
            if len(recent) > 2048:
                recent.popitem(last=False)
            return False

    def _enqueue(self, scope: _DecisionScope, kind: str, payload: dict[str, Any], *,
                 actor_type: str, actor_id: str, identity: Any) -> None:
        self._transport.enqueue(_Work(
            decision_id=scope.decision_id, kind=kind, payload=payload,
            actor_type=actor_type, actor_id=actor_id,
            # Same decision may be opened in several independent kickoffs.
            # Distinct scope run IDs prevent new executions from colliding
            # with earlier tool ordinals or LLM provider call IDs.
            idempotency_key=_key(scope.decision_id, scope.run_id, kind, identity),
        ))

    def _observe_llm(self, event: Any) -> None:
        try:
            scope = self._scope.get()
            if scope is None:
                return
            decision_id = scope.decision_id
            call_id = getattr(event, "call_id", None)
            # CrewAI exposes call_id for model events; prefer it over response content.
            if not call_id:
                self._error("llm_missing_call_id", decision_id, ValueError("missing CrewAI call_id"))
                return
            identity = (str(call_id), str(getattr(event, "agent_id", "")),
                        str(getattr(event, "task_id", "")))
            with self._lock:
                known = scope.seen_llm
                if identity in known:
                    known.move_to_end(identity)
                    return
                known[identity] = None
                if len(known) > 2048:
                    known.popitem(last=False)
            output = getattr(event, "response", None)
            payload: dict[str, Any] = {
                "framework": "crewai", "source": "crewai.events.LLMCallCompletedEvent",
                "call_id": str(call_id), "model": getattr(event, "model", None),
                "agent_id": getattr(event, "agent_id", None),
                "agent_role": getattr(event, "agent_role", None),
                "task_id": getattr(event, "task_id", None),
                "call_type": str(getattr(event, "call_type", "")),
                "status": "completed", "response_commitment": _commitment(output),
            }
            if self.capture_content:
                payload["response"] = output
            self._enqueue(scope, "model_completed", payload,
                          actor_type="agent", actor_id=self.agent_id,
                          identity=("llm", *identity))
        except Exception as exc:
            self._error("llm_observer", self.current_decision_id, exc)

    def _observe_tool_finished(self, event: Any) -> None:
        try:
            scope = self._scope.get()
            if scope is None:
                return
            decision_id = scope.decision_id
            # Cache hit is NOT execution. Guarded rejection must not become a tool action.
            if getattr(event, "from_cache", False):
                return
            output = getattr(event, "output", None)
            if isinstance(output, str) and output.startswith("Tool execution blocked by hook"):
                return
            if self._seen_event(scope, event):
                return
            ordinal = self._next(scope)
            tool_name = getattr(event, "tool_name", None)
            _require("tool_name", tool_name)
            agent_id = str(getattr(event, "agent_id", None) or self.agent_id)
            arguments = getattr(event, "tool_args", None)
            base = {
                "framework": "crewai", "source": "crewai.events.ToolUsageFinishedEvent",
                "tool_name": tool_name, "agent_id": getattr(event, "agent_id", None),
                "agent_role": getattr(event, "agent_role", None),
                "task_id": getattr(event, "task_id", None),
                "occurrence": ordinal,
                "tool_args_commitment": _commitment(arguments),
            }
            request = dict(base)
            if self.capture_content:
                request["tool_args"] = arguments
            failure = getattr(event, "failure", None)
            executed = dict(base)
            executed.update({
                "status": "failed" if failure is not None else "completed",
                "output_commitment": _commitment(output),
                "failure_reported": failure is not None,
            })
            if self.capture_content:
                executed["output"] = output
            # Paired post-fact emission deliberately avoids inventing a request for
            # rejected tools; never claim an external business outcome here.
            self._enqueue(scope, "tool_requested", request,
                          actor_type="tool", actor_id=str(tool_name), identity=("tool", ordinal, "request"))
            self._enqueue(scope, "tool_executed", executed,
                          actor_type="tool", actor_id=str(tool_name), identity=("tool", ordinal, "execution"))
        except Exception as exc:
            self._error("tool_observer", self.current_decision_id, exc)
