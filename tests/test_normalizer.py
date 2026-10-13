"""The normalizer keeps only the fields of data contract version 1."""

from __future__ import annotations

import json

import pytest

from custom_components.event_blackbox.normalizer import (
    EventKind,
    EventRecord,
    normalize_automation_triggered,
    normalize_script_started,
    normalize_service_call,
    normalize_state_change,
    record_size,
    restore_record,
)

from .conftest import SYNTHETIC_SECRETS

CTX = "01SYNTHETICCONTEXT00000001"
PARENT = "01SYNTHETICCONTEXT00000000"


def _allow(entity_id: str) -> bool:
    return entity_id in {"light.demo_hall", "light.demo_desk"}


def test_state_change_record_has_contract_fields_only() -> None:
    """A state change becomes a record with exactly the contract fields."""
    record = normalize_state_change(
        seq=1,
        relative_ms=100,
        entity_id="light.demo_hall",
        old_state="off",
        new_state="on",
        context_id=CTX,
        parent_id=None,
    )
    assert record is not None
    assert record.as_dict() == {
        "schema": 1,
        "event_id": 1,
        "relative_ms": 100,
        "kind": "state_change",
        "entity_ref": "light.demo_hall",
        "transition": "off_to_on",
        "context_ref": CTX,
        "parent_context_ref": None,
        "service": None,
        "target_refs": [],
    }


@pytest.mark.parametrize(
    ("old", "new", "transition"),
    [
        (None, "on", "none_to_on"),
        ("on", None, "on_to_none"),
        ("off", "off", "unchanged"),
        ("ON", "Off", "on_to_off"),
        ("21.5", "22.0", "value_to_value"),
        ("unavailable", "36.6", "unavailable_to_value"),
        ("-3", "1e3", "value_to_value"),
        ("nan", "inf", "value_to_value"),
        ("unavailable", "on", "unavailable_to_on"),
        ("on", "<script>alert(1)</script>", "on_to_other"),
        ("on", "x" * 33, "on_to_other"),
        ("on", "Grüße", "on_to_other"),
        ("on", "", "on_to_other"),
    ],
)
def test_transition_is_bounded_and_sanitized(
    old: str | None, new: str | None, transition: str
) -> None:
    """Only short, plain state tokens survive; anything else becomes 'other'."""
    record = normalize_state_change(
        seq=1,
        relative_ms=0,
        entity_id="sensor.demo_value",
        old_state=old,
        new_state=new,
        context_id=CTX,
        parent_id=None,
    )
    assert record is not None
    assert record.transition == transition


@pytest.mark.parametrize(
    "entity_id",
    ["", "light", "Light.Demo", "light.demo_x hall", "light." + "x" * 300, "a.b.c"],
)
def test_invalid_entity_ids_are_rejected(entity_id: str) -> None:
    """Malformed or oversized entity IDs produce no record."""
    assert (
        normalize_state_change(
            seq=1,
            relative_ms=0,
            entity_id=entity_id,
            old_state="off",
            new_state="on",
            context_id=CTX,
            parent_id=None,
        )
        is None
    )


@pytest.mark.parametrize("context_id", ["", "x" * 65, "has space", "<ctx>", None])
def test_invalid_context_ids_are_rejected(context_id: str | None) -> None:
    """A record always carries a plain, bounded context ID."""
    assert (
        normalize_state_change(
            seq=1,
            relative_ms=0,
            entity_id="light.demo_hall",
            old_state="off",
            new_state="on",
            context_id=context_id,  # type: ignore[arg-type]
            parent_id=None,
        )
        is None
    )


def test_invalid_parent_context_is_dropped_not_trusted() -> None:
    """A malformed parent ID is treated as missing, never kept."""
    record = normalize_state_change(
        seq=1,
        relative_ms=0,
        entity_id="light.demo_hall",
        old_state="off",
        new_state="on",
        context_id=CTX,
        parent_id="<bad parent>",
    )
    assert record is not None
    assert record.parent_context_ref is None


def test_automation_triggered_record() -> None:
    """Automation runs keep entity and both context IDs."""
    record = normalize_automation_triggered(
        seq=2,
        relative_ms=36,
        entity_id="automation.demo_hall_light",
        context_id=CTX,
        parent_id=PARENT,
    )
    assert record is not None
    assert record.kind is EventKind.AUTOMATION_TRIGGERED
    assert (record.entity_ref, record.parent_context_ref) == (
        "automation.demo_hall_light",
        PARENT,
    )
    assert record.transition is None


def test_script_started_record() -> None:
    """Script runs keep entity and context."""
    record = normalize_script_started(
        seq=3,
        relative_ms=40,
        entity_id="script.demo_scene",
        context_id=CTX,
        parent_id=None,
    )
    assert record is not None
    assert record.kind is EventKind.SCRIPT_STARTED


def test_service_call_keeps_only_allowlisted_targets() -> None:
    """Service data is dropped; only allowlisted entity targets remain."""
    record = normalize_service_call(
        seq=4,
        relative_ms=88,
        domain="light",
        service="turn_on",
        service_data={
            **SYNTHETIC_SECRETS,
            "entity_id": ["light.demo_hall", "light.demo_secret", "light.demo_desk"],
            "brightness": 255,
        },
        context_id=CTX,
        parent_id=None,
        allowed=_allow,
    )
    assert record is not None
    assert record.service == "light.turn_on"
    assert record.target_refs == ("light.demo_desk", "light.demo_hall")
    dumped = json.dumps(record.as_dict())
    for value in SYNTHETIC_SECRETS.values():
        if value != "light.demo_hall":
            assert value not in dumped
    assert "brightness" not in dumped
    assert "demo_secret" not in dumped


@pytest.mark.parametrize(
    "service_data",
    [
        {},
        {"entity_id": "light.demo_other"},
        {"entity_id": ["light.demo_other"]},
        {"entity_id": 42},
        {"area_id": "demo_area"},
        {"entity_id": ["light.demo_hall" + "x" * 300]},
    ],
)
def test_service_call_without_allowlisted_target_is_dropped(
    service_data: dict[str, object],
) -> None:
    """Calls that do not touch the allowlist are not recorded at all."""
    assert (
        normalize_service_call(
            seq=1,
            relative_ms=0,
            domain="light",
            service="turn_on",
            service_data=service_data,
            context_id=CTX,
            parent_id=None,
            allowed=_allow,
        )
        is None
    )


def test_service_call_accepts_single_entity_string() -> None:
    """entity_id can be a string as well as a list."""
    record = normalize_service_call(
        seq=1,
        relative_ms=0,
        domain="light",
        service="turn_off",
        service_data={"entity_id": "light.demo_hall"},
        context_id=CTX,
        parent_id=None,
        allowed=_allow,
    )
    assert record is not None
    assert record.target_refs == ("light.demo_hall",)


@pytest.mark.parametrize(
    ("domain", "service"),
    [("Light", "turn_on"), ("light", "turn on"), ("light", "x" * 65), ("", "x")],
)
def test_service_call_with_invalid_name_is_dropped(domain: str, service: str) -> None:
    """Only plain domain and service names are accepted."""
    assert (
        normalize_service_call(
            seq=1,
            relative_ms=0,
            domain=domain,
            service=service,
            service_data={"entity_id": "light.demo_hall"},
            context_id=CTX,
            parent_id=None,
            allowed=_allow,
        )
        is None
    )


def test_service_call_caps_number_of_targets() -> None:
    """A call with a huge target list keeps a bounded number of targets."""
    many = [f"light.demo_{i}" for i in range(500)]
    record = normalize_service_call(
        seq=1,
        relative_ms=0,
        domain="light",
        service="turn_on",
        service_data={"entity_id": many},
        context_id=CTX,
        parent_id=None,
        allowed=lambda _entity: True,
    )
    assert record is not None
    assert len(record.target_refs) == 32


def test_record_size_grows_with_content() -> None:
    """The size estimate is positive and reflects variable-length fields."""
    small = EventRecord(
        event_id=1,
        relative_ms=0,
        kind=EventKind.STATE_CHANGE,
        entity_ref="light.demo_a",
        transition="off_to_on",
        context_ref=CTX,
        parent_context_ref=None,
    )
    large = EventRecord(
        event_id=1,
        relative_ms=0,
        kind=EventKind.SERVICE_CALL,
        entity_ref=None,
        transition=None,
        context_ref=CTX,
        parent_context_ref=PARENT,
        service="light.turn_on",
        target_refs=tuple(f"light.demo_{i}" for i in range(10)),
    )
    assert 0 < record_size(small) < record_size(large)


@pytest.mark.parametrize(
    ("old", "new", "transition"),
    [
        ("home", "not_home", "changed"),
        ("demo_zone", "demo_zone", "unchanged"),
        (None, "locked", "appeared"),
        ("home", None, "removed"),
    ],
)
def test_minimal_transition_hides_states(
    old: str | None, new: str | None, transition: str
) -> None:
    """For sensitive domains only the fact of a change is kept, never the states."""
    record = normalize_state_change(
        seq=1,
        relative_ms=0,
        entity_id="person.demo_resident",
        old_state=old,
        new_state=new,
        context_id=CTX,
        parent_id=None,
        minimal=True,
    )
    assert record is not None
    assert record.transition == transition


# --- restoring stored history ------------------------------------

STORED = {
    "schema": 1,
    "event_id": 9,
    "relative_ms": 5,
    "kind": "service_call",
    "entity_ref": None,
    "transition": None,
    "context_ref": CTX,
    "parent_context_ref": PARENT,
    "service": "light.turn_on",
    "target_refs": ["light.demo_hall"],
}


def test_restore_record_accepts_valid_data_with_new_position() -> None:
    """A valid stored record comes back with the given sequence and time."""
    record = restore_record(STORED, event_id=1, relative_ms=-100)
    assert record is not None
    assert (record.event_id, record.relative_ms) == (1, -100)
    assert record.target_refs == ("light.demo_hall",)
    assert record.service == "light.turn_on"


@pytest.mark.parametrize(
    "change",
    [
        {"schema": 2},
        {"kind": "nonsense"},
        {"kind": 5},
        {"context_ref": "<bad>"},
        {"parent_context_ref": "<bad>"},
        {"entity_ref": "Not An Entity"},
        {"entity_ref": "light.demo_hall"},
        {"transition": "x" * 100},
        {"transition": 3},
        {"service": "no service"},
        {"target_refs": "light.demo_hall"},
        {"target_refs": ["bad target"]},
        {"target_refs": [f"light.demo_{i}" for i in range(40)]},
    ],
)
def test_restore_record_rejects_manipulated_data(change: dict[str, object]) -> None:
    """Anything outside data contract version 1 is dropped."""
    assert restore_record({**STORED, **change}, event_id=1, relative_ms=0) is None


def test_restore_record_rejects_non_mapping() -> None:
    """Only mappings can be records."""
    assert restore_record(["not", "a", "record"], event_id=1, relative_ms=0) is None
