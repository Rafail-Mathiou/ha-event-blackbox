"""The read-only "why" report built from recorded events."""

from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest

from custom_components.event_blackbox.normalizer import EventKind, EventRecord
from custom_components.event_blackbox.query import build_overview, build_report

from .test_correlation import AUTO, HALL, MOTION, MOTION_B, call, run, st

STARTED = datetime(2026, 1, 1, 2, 17, 0, tzinfo=UTC)


def test_confirmed_chain_report() -> None:
    """A confirmed chain is reported root first, with times and labels."""
    records = (
        st(1, 100, MOTION, "CTX_A"),
        run(2, 136, AUTO, "CTX_B", "CTX_A"),
        call(3, 188, (HALL,), "CTX_B"),
        st(4, 260, HALL, "CTX_B"),
    )
    report = build_report(entity_id=HALL, records=records, started_at=STARTED)

    assert report["status"] == "explained"
    assert report["evidence"] == "CONFIRMED_BY_CONTEXT"
    assert report["change"]["time"] == "2026-01-01T02:17:00.260000+00:00"
    assert report["change"]["transition"] == "off_to_on"
    assert [
        (s["kind"], s.get("entity_id") or s.get("service")) for s in report["chain"]
    ] == [
        ("state_change", MOTION),
        ("automation_triggered", AUTO),
        ("service_call", "light.turn_on"),
    ]
    assert [s["link"] for s in report["chain"]] == [
        "PARENT_CONTEXT",
        "SAME_CONTEXT",
        "SAME_CONTEXT",
    ]
    assert report["candidates"] == []
    assert report["summary"] == (
        "light.demo_hall changed (off_to_on). Confirmed by context: "
        "binary_sensor.demo_motion (off_to_on), then automation.demo_hall_light ran, "
        "then light.turn_on was called."
    )


def test_report_contains_no_context_ids() -> None:
    """Context IDs stay internal; the report names events, not contexts."""
    records = (call(1, 90, (HALL,), "CTX_SECRET_U"), st(2, 100, HALL, "CTX_SECRET_U"))
    report = build_report(entity_id=HALL, records=records, started_at=STARTED)
    assert "CTX_SECRET" not in json.dumps(report)


def test_direct_action_report() -> None:
    """A service call without automation is reported as a direct action."""
    records = (call(1, 90, (HALL,), "CTX_U"), st(2, 100, HALL, "CTX_U"))
    report = build_report(entity_id=HALL, records=records, started_at=STARTED)
    assert report["evidence"] == "CONFIRMED_BY_CONTEXT"
    assert "DIRECT_ACTION" in report["notes"]
    assert report["summary"].endswith(
        "It was a direct action, for example from the UI, an app or the API."
    )


def test_temporal_candidates_report() -> None:
    """Without context, candidates are listed and clearly not confirmed."""
    records = (
        st(1, 1000, MOTION, "CTX_A"),
        st(2, 1000, MOTION_B, "CTX_B"),
        st(3, 1100, HALL, "CTX_X"),
    )
    report = build_report(entity_id=HALL, records=records, started_at=STARTED)
    assert report["evidence"] == "INFERRED_TEMPORAL"
    assert report["chain"] == []
    assert [c["entity_id"] for c in report["candidates"]] == [MOTION, MOTION_B]
    assert all(c["evidence"] == "INFERRED_TEMPORAL" for c in report["candidates"])
    assert report["summary"] == (
        "light.demo_hall changed (off_to_on). No context link was recorded. "
        "Possible causes, not confirmed: binary_sensor.demo_motion (off_to_on), "
        "binary_sensor.demo_motion_b (off_to_on)."
    )


def test_unresolved_report() -> None:
    """With nothing to go on, the report says so explicitly."""
    report = build_report(
        entity_id=HALL, records=(st(1, 100, HALL, "CTX_X"),), started_at=STARTED
    )
    assert report["evidence"] == "UNRESOLVED"
    assert report["summary"] == (
        "light.demo_hall changed (off_to_on). The cause cannot be determined from "
        "the recorded events."
    )


def test_no_change_recorded() -> None:
    """An entity without a recorded change gets a clear status, not an error."""
    report = build_report(
        entity_id=HALL, records=(st(1, 100, MOTION, "CTX_A"),), started_at=STARTED
    )
    assert report["status"] == "no_change_recorded"
    assert report["evidence"] == "UNRESOLVED"
    assert report["change"] is None
    assert report["recorded_changes"] == 0
    assert report["summary"] == (
        "No change of light.demo_hall has been recorded since recording started."
    )


def test_latest_change_is_explained_and_counted() -> None:
    """The newest change is explained; earlier ones are counted."""
    records = (
        st(1, 100, HALL, "CTX_1"),
        call(2, 190, (HALL,), "CTX_U"),
        st(3, 200, HALL, "CTX_U"),
    )
    report = build_report(entity_id=HALL, records=records, started_at=STARTED)
    assert report["recorded_changes"] == 2
    assert report["change"]["time"].endswith("02:17:00.200000+00:00")
    assert report["evidence"] == "CONFIRMED_BY_CONTEXT"


def test_recent_changes_are_listed_latest_first() -> None:
    """The report lists recent changes with their sequence numbers for the panel."""
    records = tuple(st(i, i * 100, HALL, f"CTX_{i}") for i in range(1, 26))
    report = build_report(entity_id=HALL, records=records, started_at=STARTED)
    changes = report["changes"]
    assert len(changes) == 20
    assert changes[0]["event_id"] == 25
    assert changes[-1]["event_id"] == 6
    assert set(changes[0]) == {"event_id", "time", "transition", "selected"}
    assert changes[0]["selected"] is True
    assert not any(c["selected"] for c in changes[1:])


def test_a_specific_change_can_be_explained() -> None:
    """Passing a sequence number explains that change instead of the latest."""
    records = (
        call(1, 90, (HALL,), "CTX_U"),
        st(2, 100, HALL, "CTX_U"),
        st(3, 500, HALL, "CTX_X"),
    )
    report = build_report(
        entity_id=HALL, records=records, started_at=STARTED, event_id=2
    )
    assert report["evidence"] == "CONFIRMED_BY_CONTEXT"
    assert [c["selected"] for c in report["changes"]] == [False, True]


@pytest.mark.parametrize("event_id", [1, 999])
def test_unknown_or_foreign_change_is_not_found(event_id: int) -> None:
    """A sequence number that is no change of this entity gives a clear status."""
    records = (call(1, 90, (HALL,), "CTX_U"), st(2, 100, HALL, "CTX_U"))
    report = build_report(
        entity_id=HALL, records=records, started_at=STARTED, event_id=event_id
    )
    assert report["status"] == "change_not_found"
    assert report["change"] is None


# --- overview for the monitor (panel design) --------------------------------


def test_overview_lists_entities_activity_and_evidence() -> None:
    """The monitor gets per-entity summaries, a newest-first tape and totals."""
    records = (
        st(1, 100, MOTION, "CTX_A"),
        run(2, 136, AUTO, "CTX_B", "CTX_A"),
        call(3, 188, (HALL,), "CTX_B"),
        st(4, 260, HALL, "CTX_B"),
        st(5, 900, "light.demo_desk", "CTX_X"),
        st(6, 5000, "light.demo_lonely", "CTX_Y"),
    )
    overview = build_overview(records=records, started_at=STARTED)

    entities = {e["entity_id"]: e for e in overview["entities"]}
    assert [e["entity_id"] for e in overview["entities"]] == [
        "light.demo_lonely",
        "light.demo_desk",
        HALL,
        MOTION,
    ]
    assert entities[HALL] == {
        "entity_id": HALL,
        "changes": 1,
        "last_time": "2026-01-01T02:17:00.260000+00:00",
        "last_transition": "off_to_on",
        "last_evidence": "CONFIRMED_BY_CONTEXT",
        "initial": False,
    }
    assert entities["light.demo_desk"]["last_evidence"] == "INFERRED_TEMPORAL"
    assert entities["light.demo_lonely"]["last_evidence"] == "UNRESOLVED"
    assert overview["evidence"] == {
        "CONFIRMED_BY_CONTEXT": 1,
        "INFERRED_TEMPORAL": 1,
        "UNRESOLVED": 2,
    }
    assert [a["kind"] for a in overview["activity"][:3]] == [
        "state_change",
        "state_change",
        "state_change",
    ]
    assert overview["activity"][-1]["entity_id"] == MOTION
    assert overview["activity"][3] == {
        "time": "2026-01-01T02:17:00.188000+00:00",
        "kind": "service_call",
        "service": "light.turn_on",
        "targets": [HALL],
    }


def test_overview_activity_is_bounded() -> None:
    """Only the most recent events are sent to the monitor."""
    records = tuple(st(i, i, f"light.demo_{i % 3}", f"CTX_{i}") for i in range(1, 200))
    overview = build_overview(records=records, started_at=STARTED)
    assert len(overview["activity"]) == 30
    assert overview["activity"][0]["time"].endswith("02:17:00.199000+00:00")


def test_overview_of_empty_buffer() -> None:
    """An empty buffer gives empty lists and zero totals."""
    overview = build_overview(records=(), started_at=STARTED)
    assert overview == {
        "entities": [],
        "activity": [],
        "evidence": {
            "CONFIRMED_BY_CONTEXT": 0,
            "INFERRED_TEMPORAL": 0,
            "UNRESOLVED": 0,
        },
        "initial": 0,
    }


def test_first_state_report_and_overview() -> None:
    """A first state is explained as such and kept out of the evidence totals."""
    records = (
        EventRecord(
            event_id=1,
            relative_ms=100,
            kind=EventKind.STATE_CHANGE,
            entity_ref=HALL,
            transition="none_to_on",
            context_ref="CTX_1",
            parent_context_ref=None,
        ),
        st(2, 200, MOTION, "CTX_2"),
    )
    report = build_report(entity_id=HALL, records=records, started_at=STARTED)
    assert report["summary"] == (
        "light.demo_hall got its first state, for example after a restart or a "
        "reload. That is not a change with a cause."
    )
    overview = build_overview(records=records, started_at=STARTED)
    entities = {e["entity_id"]: e for e in overview["entities"]}
    assert entities[HALL]["initial"] is True
    assert entities[MOTION]["initial"] is False
    assert overview["evidence"] == {
        "CONFIRMED_BY_CONTEXT": 0,
        "INFERRED_TEMPORAL": 0,
        "UNRESOLVED": 1,
    }
    assert overview["initial"] == 1


def test_removed_entity_report() -> None:
    """A removal is explained as such, not as an unknown cause."""
    records = (
        EventRecord(
            event_id=1,
            relative_ms=100,
            kind=EventKind.STATE_CHANGE,
            entity_ref=HALL,
            transition="on_to_none",
            context_ref="CTX_1",
            parent_context_ref=None,
        ),
    )
    report = build_report(entity_id=HALL, records=records, started_at=STARTED)
    assert report["summary"] == (
        "light.demo_hall was removed from Home Assistant. That is not a change with a "
        "cause."
    )
    assert "ENTITY_REMOVED" in report["notes"]
