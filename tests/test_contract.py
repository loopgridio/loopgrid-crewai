from __future__ import annotations

import concurrent.futures
import json
import threading

import pytest

from conftest import LLMCallCompletedEvent as LLM, ToolUsageFinishedEvent as TOOL


def kinds(client):
    return [a[1][1] for a in client.calls if a[0] == "add_event"]


def actions(client):
    return [a for a in client.calls if a[0] == "add_event"]


def test_unscoped_events_ignored(runtime):
    integration, client, bus = runtime
    bus.emit(LLM(call_id="id"))
    bus.emit(TOOL())
    integration.flush()
    assert not actions(client)


def test_native_event_observation(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(LLM(call_id="c1"))
        bus.emit(TOOL())
        bus.emit(LLM(call_id="c2"))
    integration.flush()
    assert kinds(client) == ["model_completed", "tool_requested", "tool_executed", "model_completed"]


def test_request_precedes_execution(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(TOOL())
    integration.flush()
    assert kinds(client) == ["tool_requested", "tool_executed"]


def test_same_tool_args_repeated_invocations_are_distinct(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(TOOL())
        bus.emit(TOOL())
    integration.flush()
    assert len(actions(client)) == 4
    keys = [a[2]["idempotency_key"] for a in actions(client)]
    assert len(set(keys)) == 4


def test_replayed_event_object_is_suppressed(runtime):
    integration, client, bus = runtime
    event = TOOL()
    with integration.decision_context("d1"):
        bus.emit(event)
        bus.emit(event)
    integration.flush()
    assert len(actions(client)) == 2


def test_cache_does_not_claim_real_execution(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(TOOL(from_cache=True))
    integration.flush()
    assert not actions(client)


def test_blocked_hook_result_not_recorded(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(TOOL(output="Tool execution blocked by hook. Tool: sandbox_refund"))
    integration.flush()
    assert not actions(client)


def test_no_raw_content_by_default(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(LLM(call_id="c1", response="SECRET-RESPONSE"))
        bus.emit(TOOL(tool_args={"password": "SECRET-INPUT"}, output="SECRET-OUTPUT"))
    integration.flush()
    rendered = json.dumps(actions(client))
    assert "SECRET-RESPONSE" not in rendered
    assert "SECRET-INPUT" not in rendered
    assert "SECRET-OUTPUT" not in rendered
    assert "sha256" in rendered


def test_capture_content_explicit(runtime):
    integration, client, bus = runtime
    integration.capture_content = True
    with integration.decision_context("d1"):
        bus.emit(LLM(call_id="c1", response="output"))
        bus.emit(TOOL(tool_args={"amount": 25}, output="tool-output"))
    integration.flush()
    rendered = json.dumps(actions(client))
    assert "tool-output" in rendered and "amount" in rendered and '"output"' in rendered


def test_llm_duplicate_id_is_idempotent(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(LLM(call_id="same"))
        bus.emit(LLM(call_id="same"))
    integration.flush()
    assert kinds(client) == ["model_completed"]


def test_llm_reused_id_other_agent_is_distinct(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(LLM(call_id="same", agent_id="agent1"))
        bus.emit(LLM(call_id="same", agent_id="agent2"))
    integration.flush()
    assert kinds(client) == ["model_completed", "model_completed"]


def test_missing_model_call_id_failure_observable(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(LLM(call_id=""))
    integration.flush()
    with pytest.raises(RuntimeError, match="llm_missing_call_id"):
        integration.assert_healthy()
    assert not actions(client)


def test_decision_context_restores_after_error(runtime):
    integration, client, bus = runtime
    with pytest.raises(ValueError):
        with integration.decision_context("d1"):
            assert integration.current_decision_id == "d1"
            raise ValueError("fail")
    assert integration.current_decision_id is None


def test_nested_contexts_restore(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        with integration.decision_context("d2"):
            bus.emit(TOOL())
        bus.emit(TOOL())
    integration.flush()
    assert [a[1][0] for a in actions(client)] == ["d2", "d2", "d1", "d1"]


def test_isolated_concurrent_decisions(runtime):
    integration, client, bus = runtime
    def run(d):
        with integration.decision_context(d):
            bus.emit(LLM(call_id="call", agent_id=d))
            bus.emit(TOOL(agent_id=d))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(run, [f"d{i}" for i in range(16)]))
    integration.flush()
    grouped = {}
    for a in actions(client):
        grouped.setdefault(a[1][0], []).append(a[1][1])
    assert len(grouped) == 16
    assert all(g == ["model_completed", "tool_requested", "tool_executed"] for g in grouped.values())


def test_failed_tool_reports_failure_not_success(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(TOOL(failure={"reason": "declined"}, output="failed"))
    integration.flush()
    assert actions(client)[1][1][2]["status"] == "failed"
    assert actions(client)[1][1][2]["failure_reported"] is True


def test_no_inferred_business_outcome(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(LLM(call_id="c1"))
        bus.emit(TOOL())
    integration.flush()
    assert "outcome_observed" not in kinds(client)
    assert "human_approved" not in kinds(client)
    assert "policy_evaluated" not in kinds(client)


def test_explicit_ordered_review(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(LLM(call_id="c1"))
        integration.record_human_review("d1", reviewer="reviewer-1", approved=True)
        bus.emit(TOOL())
    integration.flush()
    assert [x[0] for x in client.calls] == ["add_event", "human_approved", "add_event", "add_event"]


def test_explicit_rejection(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(LLM(call_id="c1"))
        integration.record_human_review("d1", reviewer="reviewer-1", approved=False)
    integration.flush()
    assert kinds(client) == ["model_completed"]
    assert [a[0] for a in client.calls] == ["add_event", "human_rejected"]


def test_review_rejects_blank_identity(runtime):
    integration, client, bus = runtime
    with pytest.raises(ValueError, match="reviewer"):
        integration.record_human_review("d", reviewer="", approved=True)


def test_outcome_after_queued_events(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d1"):
        bus.emit(TOOL())
        integration.record_outcome("d1", {"status": "succeeded"})
    assert [a[0] for a in client.calls] == ["add_event", "add_event", "outcome_observed"]


def test_failures_survive_background_worker(runtime):
    integration, client, bus = runtime
    client.fail = True
    with integration.decision_context("d1"):
        bus.emit(TOOL())
    integration.flush()
    with pytest.raises(RuntimeError, match="transport"):
        integration.assert_healthy()


def test_explicit_policy_requirement(runtime):
    integration, client, bus = runtime
    for policy in ({"decision": "unknown", "policy_id": "p"}, {"decision": "auto_allowed"}):
        with pytest.raises(ValueError):
            integration.record_policy("d1", policy)


def test_start_decision_enforces_authority(runtime):
    integration, client, bus = runtime
    with pytest.raises(ValueError, match="authority"):
        integration.start_decision(decision_type="refund", agent={"id":"a"}, authority={}, model={"name":"m"}, context={"version":1}, proposed_action={"tool":"refund"})


def test_start_decision_with_policy(runtime):
    integration, client, bus = runtime
    r = integration.start_decision(
        decision_type="refund", agent={"id": "a"}, authority={"acting_for":"demo"},
        model={"name":"m"}, context={"version":1}, proposed_action={"tool":"refund"},
        policy={"policy_id":"p", "decision":"auto_allowed"},
    )
    assert r["decision_id"] == "dec_1"
    assert [x[0] for x in client.calls] == ["record_decision", "policy_evaluated"]


def test_new_instance_uninstalled_no_duplicate_handlers(runtime):
    integration, client, bus = runtime
    integration.install()
    with integration.decision_context("d1"):
        bus.emit(LLM(call_id="c1"))
    integration.flush()
    assert kinds(client) == ["model_completed"]


def test_no_secrets_in_error_messages_on_healthy(runtime):
    integration, client, bus = runtime
    integration.assert_healthy()


def test_key_computation_stable(runtime):
    integration, client, bus = runtime
    from loopgrid_crewai.integration import _key
    assert _key("d", "tool", 1) == _key("d", "tool", 1)
    assert _key("d", "tool", 1) != _key("d", "tool", 2)


def test_native_bus_delayed_callbacks_are_drained_before_scope_exit(runtime):
    """Regression: CrewAI eventbus callbacks run asynchronously in a pool.

    Context must be captured on emit; drain the bus before resetting it.
    """
    import contextvars
    import time

    integration, client, bus = runtime
    original_emit = bus.emit
    original_flush = bus.flush
    pending = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        def delayed_emit(event):
            ctx = contextvars.copy_context()
            def callback():
                time.sleep(0.01)
                ctx.run(original_emit, event)
            pending.append(executor.submit(callback))

        def delayed_flush(timeout=30.0):
            for future in pending[:]:
                future.result(timeout=timeout)
            pending.clear()
            return True

        bus.emit = delayed_emit
        bus.flush = delayed_flush
        with integration.decision_context("async-observed"):
            bus.emit(LLM(call_id="delayed-call"))
        assert integration.current_decision_id is None
        assert kinds(client) == ["model_completed"]
        bus.emit = original_emit
        bus.flush = original_flush


def test_native_bus_flush_timeout_resets_context(runtime):
    integration, client, bus = runtime
    original_flush = bus.flush
    bus.flush = lambda timeout=30.0: False
    try:
        with pytest.raises(TimeoutError, match="CrewAI event handlers"):
            with integration.decision_context("timed-out"):
                pass
        assert integration.current_decision_id is None
    finally:
        bus.flush = original_flush


def test_transport_failure_redacts_untrusted_exception(runtime):
    integration, client, bus = runtime
    class DangerousClientError(RuntimeError):
        pass
    def secret_failure(*args, **kwargs):
        raise DangerousClientError("token=SENSITIVE-API-KEY&payload=private-data")
    client.add_event = secret_failure
    with integration.decision_context("d-redact"):
        bus.emit(LLM(call_id="c-privacy"))
    with pytest.raises(RuntimeError) as result:
        integration.assert_healthy()
    message = str(result.value)
    assert "transport" in message and "DangerousClientError" in message
    assert "SENSITIVE-API-KEY" not in message and "private-data" not in message


def test_shutdown_idempotent_and_disables_future_scopes(runtime):
    integration, client, bus = runtime
    with integration.decision_context("d-before"):
        bus.emit(LLM(call_id="before-close"))
    integration.shutdown()
    integration.shutdown()
    with pytest.raises(RuntimeError, match="shut down"):
        with integration.decision_context("d-after"):
            pass
    with pytest.raises(RuntimeError, match="shut down"):
        integration.install()
    # A process-level listener may stay registered but must become inert.
    bus.emit(LLM(call_id="after-close"))
    assert kinds(client) == ["model_completed"]


def test_transport_cannot_enqueue_after_shutdown(runtime):
    integration, client, bus = runtime
    integration.shutdown()
    from loopgrid_crewai.integration import _Work
    with pytest.raises(RuntimeError, match="closed"):
        integration._transport.enqueue(_Work("d", "model_completed", {}, "agent", "a", "unique"))


def test_finished_scope_does_not_accumulate_owner_bookkeeping(runtime):
    """Expired scopes, including their LRU event references, can be collected."""
    import gc
    import weakref

    integration, client, bus = runtime
    with integration.decision_context("one-shot"):
        scope_ref = weakref.ref(integration._scope.get())
        bus.emit(LLM(call_id="c-one"))
        bus.emit(TOOL())
    assert integration.current_decision_id is None
    gc.collect()
    assert scope_ref() is None
    assert not hasattr(integration, "_seq")
    assert not hasattr(integration, "_seen")
    assert not hasattr(integration, "_seen_llm")


def test_reopening_same_decision_uses_distinct_tool_idempotency_keys(runtime):
    """Reopening a decision must not discard distinct repeated tool executions."""
    integration, client, bus = runtime
    for _ in range(3):
        with integration.decision_context("reopened-decision"):
            bus.emit(LLM(call_id="provider-reused-call-id"))
            bus.emit(TOOL(tool_args={"amount": 25}))
    integration.flush()
    emitted = actions(client)
    assert len(emitted) == 9
    assert kinds(client) == ["model_completed", "tool_requested", "tool_executed"] * 3
    assert len({call[2]["idempotency_key"] for call in emitted}) == 9
    assert {call[1][0] for call in emitted} == {"reopened-decision"}


def test_parallel_scopes_same_decision_dont_share_dedupe_or_ordinals(runtime):
    """Concurrent executions may share a decision ID without losing events."""
    integration, client, bus = runtime

    def run(index):
        with integration.decision_context("shared-decision"):
            bus.emit(LLM(call_id="identical-provider-call-id"))
            bus.emit(TOOL(tool_args={"amount": index}))

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(run, range(16)))
    integration.flush()
    emitted = actions(client)
    assert len(emitted) == 48
    assert len({call[2]["idempotency_key"] for call in emitted}) == 48
    assert kinds(client).count("tool_executed") == 16
    assert kinds(client).count("model_completed") == 16


def test_dedup_windows_are_bounded_within_one_long_scope(runtime):
    integration, client, bus = runtime
    with integration.decision_context("long-scope"):
        state = integration._scope.get()
        for n in range(2100):
            bus.emit(LLM(call_id=f"c{n}"))
            bus.emit(TOOL(tool_args={"amount": n}))
        assert len(state.seen_tools) <= 2048
        assert len(state.seen_llm) <= 2048
    integration.flush()
    assert kinds(client).count("tool_executed") == 2100
    assert kinds(client).count("model_completed") == 2100


def test_error_history_is_bounded(runtime):
    integration, client, bus = runtime
    for i in range(300):
        integration._error("simulated", "d", RuntimeError(f"secret_{i}"))
    assert len(integration._errors) == 256
    with pytest.raises(RuntimeError, match="simulated") as ex:
        integration.assert_healthy()
    assert "secret_299" not in str(ex.value)
