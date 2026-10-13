"""Deterministic causal graph with evidence labels.

Covers causality (direct context gives
CONFIRMED_BY_CONTEXT, closeness in time never does, missing context gives
UNRESOLVED), competing triggers (all candidates, no invented single cause) and
ordering (out-of-order input and identical timestamps are deterministic).
"""

from __future__ import annotations

import random
import time

import pytest

from custom_components.event_blackbox.correlation import (
    MAX_CANDIDATES,
    MAX_DEPTH,
    TEMPORAL_WINDOW_MS,
    CausalGraph,
    Evidence,
    LinkReason,
    Note,
    ordered,
)
from custom_components.event_blackbox.normalizer import EventKind, EventRecord

MOTION = "binary_sensor.demo_motion"
MOTION_B = "binary_sensor.demo_motion_b"
HALL = "light.demo_hall"
AUTO = "automation.demo_hall_light"


def st(
    seq: int, ms: int, entity: str, ctx: str, parent: str | None = None
) -> EventRecord:
    """Build a state change record."""
    return EventRecord(
        event_id=seq,
        relative_ms=ms,
        kind=EventKind.STATE_CHANGE,
        entity_ref=entity,
        transition="off_to_on",
        context_ref=ctx,
        parent_context_ref=parent,
    )


def run(  # noqa: PLR0913 - mirrors the record fields
    seq: int,
    ms: int,
    entity: str,
    ctx: str,
    parent: str | None,
    *,
    kind: EventKind = EventKind.AUTOMATION_TRIGGERED,
) -> EventRecord:
    """Build an automation or script run record."""
    return EventRecord(
        event_id=seq,
        relative_ms=ms,
        kind=kind,
        entity_ref=entity,
        transition=None,
        context_ref=ctx,
        parent_context_ref=parent,
    )


def call(
    seq: int, ms: int, targets: tuple[str, ...], ctx: str, parent: str | None = None
) -> EventRecord:
    """Build a service call record."""
    return EventRecord(
        event_id=seq,
        relative_ms=ms,
        kind=EventKind.SERVICE_CALL,
        entity_ref=None,
        transition=None,
        context_ref=ctx,
        parent_context_ref=parent,
        service="light.turn_on",
        target_refs=targets,
    )


def _chain_ids(graph: CausalGraph, event_id: int) -> list[tuple[int, int]]:
    explanation = graph.explain(event_id)
    assert explanation is not None
    return [(link.cause.event_id, link.effect.event_id) for link in explanation.chain]


AUTOMATION_CHAIN = (
    st(1, 100, MOTION, "CTX_A"),
    run(2, 136, AUTO, "CTX_B", "CTX_A"),
    call(3, 188, (HALL,), "CTX_B"),
    st(4, 260, HALL, "CTX_B"),
)


# --- causality ---------------------------------------------------------------


def test_context_chain_is_confirmed() -> None:
    """Trigger, automation, service call and effect are linked by context."""
    graph = CausalGraph(AUTOMATION_CHAIN)
    explanation = graph.explain(4)
    assert explanation is not None
    assert explanation.evidence is Evidence.CONFIRMED_BY_CONTEXT
    assert [
        (link.cause.event_id, link.effect.event_id, link.reason)
        for link in explanation.chain
    ] == [
        (1, 2, LinkReason.PARENT_CONTEXT),
        (2, 3, LinkReason.SAME_CONTEXT),
        (3, 4, LinkReason.SAME_CONTEXT),
    ]
    assert all(
        link.evidence is Evidence.CONFIRMED_BY_CONTEXT for link in explanation.chain
    )
    assert explanation.candidates == ()
    assert explanation.chain[0].cause.event_id == 1


def test_closeness_in_time_is_never_confirmed() -> None:
    """Without a context link, a nearby event is only a hypothesis."""
    graph = CausalGraph((st(1, 100, MOTION, "CTX_A"), st(2, 150, HALL, "CTX_X")))
    explanation = graph.explain(2)
    assert explanation is not None
    assert explanation.evidence is Evidence.INFERRED_TEMPORAL
    assert explanation.chain == ()
    assert [c.cause.event_id for c in explanation.candidates] == [1]
    assert all(c.evidence is Evidence.INFERRED_TEMPORAL for c in explanation.candidates)
    assert all(c.reason is LinkReason.TIME_WINDOW for c in explanation.candidates)
    assert Note.NO_CONTEXT_LINK in explanation.notes


def test_missing_context_is_unresolved() -> None:
    """Nothing linked and nothing nearby: the cause cannot be determined."""
    graph = CausalGraph((st(1, 100, HALL, "CTX_X"),))
    explanation = graph.explain(1)
    assert explanation is not None
    assert explanation.evidence is Evidence.UNRESOLVED
    assert explanation.chain == ()
    assert explanation.candidates == ()
    assert Note.NO_CONTEXT_LINK in explanation.notes


def test_direct_action_is_confirmed_and_marked() -> None:
    """A service call without automation (UI, app, API) is a direct action."""
    graph = CausalGraph((call(1, 90, (HALL,), "CTX_U"), st(2, 100, HALL, "CTX_U")))
    explanation = graph.explain(2)
    assert explanation is not None
    assert explanation.evidence is Evidence.CONFIRMED_BY_CONTEXT
    assert _chain_ids(graph, 2) == [(1, 2)]
    assert Note.DIRECT_ACTION in explanation.notes


def test_parent_not_recorded_keeps_confirmed_part() -> None:
    """When the trigger was not recorded, the known part stays confirmed."""
    graph = CausalGraph(AUTOMATION_CHAIN[1:])
    explanation = graph.explain(4)
    assert explanation is not None
    assert explanation.evidence is Evidence.CONFIRMED_BY_CONTEXT
    assert _chain_ids(graph, 4) == [(2, 3), (3, 4)]
    assert Note.PARENT_NOT_RECORDED in explanation.notes


def test_script_run_is_part_of_the_chain() -> None:
    """A script started by an automation shares its context and is linked."""
    records = (
        st(1, 100, MOTION, "CTX_A"),
        run(2, 130, AUTO, "CTX_B", "CTX_A"),
        run(
            3, 140, "script.demo_scene", "CTX_B", "CTX_A", kind=EventKind.SCRIPT_STARTED
        ),
        call(4, 150, (HALL,), "CTX_B"),
        st(5, 200, HALL, "CTX_B"),
    )
    graph = CausalGraph(records)
    assert _chain_ids(graph, 5) == [(1, 2), (2, 3), (3, 4), (4, 5)]


def test_sibling_state_changes_are_not_causes() -> None:
    """Other effects of the same service call are not causes of each other."""
    records = (
        call(1, 100, ("light.demo_desk", HALL), "CTX_U"),
        st(2, 120, "light.demo_desk", "CTX_U"),
        st(3, 130, HALL, "CTX_U"),
    )
    graph = CausalGraph(records)
    assert _chain_ids(graph, 3) == [(1, 3)]


def test_service_call_for_another_entity_is_not_the_cause() -> None:
    """Within a context, only a call that targets the entity is linked."""
    records = (
        run(1, 100, AUTO, "CTX_B", None),
        call(2, 110, ("light.demo_desk",), "CTX_B"),
        st(3, 150, HALL, "CTX_B"),
    )
    graph = CausalGraph(records)
    assert _chain_ids(graph, 3) == [(1, 3)]


# --- competing triggers ------------------------------------------------------


def test_two_simultaneous_triggers_give_two_candidates() -> None:
    """Two motion sensors at the same time: both listed, none chosen."""
    records = (
        st(1, 1000, MOTION, "CTX_A"),
        st(2, 1000, MOTION_B, "CTX_B"),
        st(3, 1100, HALL, "CTX_X"),
    )
    explanation = CausalGraph(records).explain(3)
    assert explanation is not None
    assert explanation.evidence is Evidence.INFERRED_TEMPORAL
    assert explanation.chain == ()
    assert [c.cause.entity_ref for c in explanation.candidates] == [MOTION, MOTION_B]
    assert Note.MULTIPLE_CANDIDATES in explanation.notes


def test_two_automations_only_the_linked_one_is_confirmed() -> None:
    """With contexts, only the automation whose context reached the light counts."""
    records = (
        st(1, 1000, MOTION, "CTX_A"),
        st(2, 1000, MOTION_B, "CTX_B"),
        run(3, 1010, AUTO, "CTX_C", "CTX_A"),
        run(4, 1011, "automation.demo_hall_light_b", "CTX_D", "CTX_B"),
        call(5, 1020, (HALL,), "CTX_C"),
        call(6, 1021, (HALL,), "CTX_D"),
        st(7, 1050, HALL, "CTX_D"),
    )
    graph = CausalGraph(records)
    explanation = graph.explain(7)
    assert explanation is not None
    assert explanation.evidence is Evidence.CONFIRMED_BY_CONTEXT
    assert _chain_ids(graph, 7) == [(2, 4), (4, 6), (6, 7)]
    assert explanation.candidates == ()


# --- ordering ----------------------------------------------------------------


def test_ordered_is_stable_for_identical_timestamps() -> None:
    """Sorting uses time first and the sequence number as tie breaker."""
    records = (st(3, 5, HALL, "C3"), st(1, 5, HALL, "C1"), st(2, 1, HALL, "C2"))
    assert [r.event_id for r in ordered(records)] == [2, 1, 3]


def test_shuffled_input_gives_identical_explanations() -> None:
    """Out-of-order input never changes the result."""
    records = (
        st(1, 1000, MOTION, "CTX_A"),
        st(2, 1000, MOTION_B, "CTX_B"),
        run(3, 1000, AUTO, "CTX_C", "CTX_A"),
        call(4, 1000, (HALL,), "CTX_C"),
        st(5, 1000, HALL, "CTX_C"),
        st(6, 1000, "light.demo_desk", "CTX_X"),
    )
    expected = {r.event_id: CausalGraph(records).explain(r.event_id) for r in records}
    rng = random.Random(4)  # noqa: S311 - deterministic shuffling for a test
    for _ in range(50):
        shuffled = list(records)
        rng.shuffle(shuffled)
        graph = CausalGraph(shuffled)
        for event_id, explanation in expected.items():
            assert graph.explain(event_id) == explanation


def test_later_events_are_never_causes() -> None:
    """Later records, or equal times with a higher sequence, are excluded."""
    records = (
        st(1, 100, HALL, "CTX_X"),
        st(2, 100, MOTION, "CTX_A"),
        st(3, 150, MOTION_B, "CTX_B"),
    )
    explanation = CausalGraph(records).explain(1)
    assert explanation is not None
    assert explanation.evidence is Evidence.UNRESOLVED


# --- robustness --------------------------------------------------------------


def test_context_cycle_terminates() -> None:
    """Contexts that point at each other do not loop forever."""
    records = (
        run(1, 100, AUTO, "CTX_A", "CTX_B"),
        run(2, 110, "automation.demo_other", "CTX_B", "CTX_A"),
        st(3, 120, HALL, "CTX_A"),
    )
    explanation = CausalGraph(records).explain(3)
    assert explanation is not None
    assert explanation.evidence is Evidence.CONFIRMED_BY_CONTEXT
    assert len(explanation.chain) <= MAX_DEPTH


def test_parent_cycle_back_into_a_visited_context_stops() -> None:
    """A parent pointing back into an already visited context ends the chain."""
    records = (
        run(1, 100, AUTO, "CTX_B", "CTX_A"),
        st(2, 200, HALL, "CTX_A", "CTX_B"),
    )
    graph = CausalGraph(records)
    assert _chain_ids(graph, 2) == [(1, 2)]


def test_very_long_chain_is_truncated() -> None:
    """A chain longer than the depth limit stops and says so."""
    records: list[EventRecord] = [st(0, 0, MOTION, "CTX_0")]
    for i in range(1, 60):
        records.append(run(i, i, f"automation.demo_{i}", f"CTX_{i}", f"CTX_{i - 1}"))
    records.append(st(60, 60, HALL, "CTX_59"))
    explanation = CausalGraph(records).explain(60)
    assert explanation is not None
    assert explanation.evidence is Evidence.CONFIRMED_BY_CONTEXT
    assert len(explanation.chain) == MAX_DEPTH
    assert Note.CHAIN_TRUNCATED in explanation.notes


def test_time_window_is_inclusive_and_bounded() -> None:
    """Candidates come from a limited window before the target only."""
    records = (
        st(1, 0, "binary_sensor.demo_too_old", "CTX_1"),
        st(2, 1, MOTION, "CTX_2"),
        st(3, 1 + TEMPORAL_WINDOW_MS, HALL, "CTX_X"),
    )
    explanation = CausalGraph(records).explain(3)
    assert explanation is not None
    assert [c.cause.event_id for c in explanation.candidates] == [2]


def test_own_entity_and_service_calls_are_not_temporal_candidates() -> None:
    """Earlier changes of the same entity and service calls are no hypotheses."""
    records = (
        st(1, 100, HALL, "CTX_1"),
        call(2, 110, ("light.demo_desk",), "CTX_2"),
        st(3, 150, HALL, "CTX_X"),
    )
    explanation = CausalGraph(records).explain(3)
    assert explanation is not None
    assert explanation.evidence is Evidence.UNRESOLVED


def test_candidates_are_capped() -> None:
    """Many nearby events give a bounded list, closest first, and a note."""
    records = [
        st(i, 1000 + i, f"binary_sensor.demo_{i}", f"CTX_{i}") for i in range(30)
    ]
    records.append(st(100, 1100, HALL, "CTX_X"))
    explanation = CausalGraph(records).explain(100)
    assert explanation is not None
    assert len(explanation.candidates) == MAX_CANDIDATES
    assert explanation.candidates[0].cause.event_id == 29
    assert Note.CANDIDATES_TRUNCATED in explanation.notes


def test_unknown_event_gives_none() -> None:
    """Explaining an event that is not in the graph returns nothing."""
    assert CausalGraph(AUTOMATION_CHAIN).explain(999) is None


@pytest.mark.parametrize("size", [10_000])
def test_large_graph_is_fast(size: int) -> None:
    """Building and querying a full buffer stays cheap."""
    records = [
        st(i, i * 10, f"binary_sensor.demo_{i % 40}", f"CTX_{i}") for i in range(size)
    ]
    started = time.perf_counter()
    graph = CausalGraph(records)
    for event_id in range(size - 100, size):
        graph.explain(event_id)
    assert time.perf_counter() - started < 5.0


# --- lifecycle events (first state, removal) ---------------------------------


def lifecycle(seq: int, ms: int, entity: str, transition: str) -> EventRecord:
    """Build a state change with a given transition."""
    return EventRecord(
        event_id=seq,
        relative_ms=ms,
        kind=EventKind.STATE_CHANGE,
        entity_ref=entity,
        transition=transition,
        context_ref=f"CTX_{seq}",
        parent_context_ref=None,
    )


def test_first_states_after_a_restart_are_not_causes_of_each_other() -> None:
    """A burst of first states (startup, reload) gives no invented candidates."""
    records = (
        lifecycle(1, 100, MOTION, "none_to_off"),
        lifecycle(2, 110, HALL, "none_to_on"),
        lifecycle(3, 120, "switch.demo_plug", "none_to_off"),
    )
    graph = CausalGraph(records)
    explanation = graph.explain(2)
    assert explanation is not None
    assert explanation.evidence is Evidence.UNRESOLVED
    assert explanation.candidates == ()
    assert Note.ENTITY_APPEARED in explanation.notes


def test_first_states_are_no_candidates_for_real_changes() -> None:
    """A real change right after startup is not blamed on first states."""
    records = (
        lifecycle(1, 100, MOTION, "none_to_off"),
        lifecycle(2, 110, "switch.demo_plug", "plug_to_none"),
        st(3, 300, HALL, "CTX_X"),
    )
    explanation = CausalGraph(records).explain(3)
    assert explanation is not None
    assert explanation.evidence is Evidence.UNRESOLVED
    assert explanation.candidates == ()


def test_removed_entity_is_noted() -> None:
    """A removal is reported as such."""
    explanation = CausalGraph((lifecycle(1, 100, HALL, "on_to_none"),)).explain(1)
    assert explanation is not None
    assert Note.ENTITY_REMOVED in explanation.notes


def test_minimal_first_state_is_a_lifecycle_event() -> None:
    """Sensitive entities appearing are recognised as lifecycle events too."""
    explanation = CausalGraph(
        (lifecycle(1, 100, "person.demo_resident", "appeared"),)
    ).explain(1)
    assert explanation is not None
    assert Note.ENTITY_APPEARED in explanation.notes


def test_first_state_can_still_be_confirmed_by_context() -> None:
    """If a context links a first state to its cause, that link is kept."""
    records = (
        call(1, 90, (HALL,), "CTX_U"),
        EventRecord(
            event_id=2,
            relative_ms=100,
            kind=EventKind.STATE_CHANGE,
            entity_ref=HALL,
            transition="none_to_on",
            context_ref="CTX_U",
            parent_context_ref=None,
        ),
    )
    explanation = CausalGraph(records).explain(2)
    assert explanation is not None
    assert explanation.evidence is Evidence.CONFIRMED_BY_CONTEXT
    assert Note.ENTITY_APPEARED not in explanation.notes
