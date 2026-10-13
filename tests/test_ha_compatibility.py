"""Compatibility of the Home Assistant event and context APIs.

These tests are the executable record of which event types and which context
semantics Home Assistant provides on the supported versions (CI matrix). They
run real core components (input_boolean, automation, script) with synthetic
demo entities only. If a Home Assistant release renames an event, drops a
payload field or changes how contexts are chained, a test here fails before
the correlation logic silently produces wrong evidence.

Background: docs/ARCHITECTURE.md, section on Home Assistant events.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.components.automation import EVENT_AUTOMATION_TRIGGERED
from homeassistant.components.script.const import EVENT_SCRIPT_STARTED
from homeassistant.const import (
    EVENT_CALL_SERVICE,
    EVENT_STATE_CHANGED,
    EVENT_STATE_REPORTED,
    MATCH_ALL,
)
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers import entity as entity_helper, event as event_helper
from homeassistant.setup import async_setup_component
import homeassistant.util.dt as dt_util
from homeassistant.util.event_type import EventType
import pytest
from pytest_homeassistant_custom_component.common import (
    MockUser,
    async_fire_time_changed,
)

from custom_components.event_blackbox.const import (
    EVENT_TYPE_AUTOMATION_TRIGGERED,
    EVENT_TYPE_CALL_SERVICE,
    EVENT_TYPE_SCRIPT_STARTED,
)

MOTION = "input_boolean.demo_motion"
HALL = "input_boolean.demo_hall"
TRACKED = {
    EVENT_STATE_CHANGED,
    EVENT_AUTOMATION_TRIGGERED,
    EVENT_CALL_SERVICE,
    EVENT_SCRIPT_STARTED,
}


@pytest.fixture
def recorded(hass: HomeAssistant) -> list[Event[Any]]:
    """Record every event of the tracked types, in firing order."""
    events: list[Event[Any]] = []

    @callback
    def _record(event: Event[Any]) -> None:
        if event.event_type in TRACKED:
            events.append(event)

    hass.bus.async_listen(MATCH_ALL, _record)
    return events


def _only(
    events: list[Event[Any]],
    event_type: str | EventType[Any],
    check: Callable[[Event[Any]], bool],
) -> Event[Any]:
    matches = [e for e in events if e.event_type == event_type and check(e)]
    assert len(matches) == 1, f"expected one {event_type} event, got {len(matches)}"
    return matches[0]


def _state_change_of(entity_id: str) -> Callable[[Event[Any]], bool]:
    return lambda e: e.data["entity_id"] == entity_id


async def _setup_booleans(hass: HomeAssistant) -> None:
    assert await async_setup_component(
        hass,
        "input_boolean",
        {"input_boolean": {"demo_motion": {}, "demo_hall": {}}},
    )


async def _setup_automation(
    hass: HomeAssistant, triggers: list[dict[str, Any]], service: str = "turn_on"
) -> None:
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "id": "demo_hall_light",
                "alias": "Demo hall light",
                "triggers": triggers,
                "actions": [
                    {
                        "action": f"input_boolean.{service}",
                        "target": {"entity_id": HALL},
                    }
                ],
            }
        },
    )
    await hass.async_block_till_done()


# --- event type names -------------------------------------------------------


def test_event_type_names_match_home_assistant() -> None:
    """The documented event type names are the ones Home Assistant fires."""
    assert EVENT_STATE_CHANGED == "state_changed"
    assert EVENT_TYPE_CALL_SERVICE == EVENT_CALL_SERVICE == "call_service"
    assert (
        EVENT_TYPE_AUTOMATION_TRIGGERED
        == EVENT_AUTOMATION_TRIGGERED
        == "automation_triggered"
    )
    assert EVENT_TYPE_SCRIPT_STARTED == EVENT_SCRIPT_STARTED == "script_started"


def test_documented_helpers_and_context_fields_exist() -> None:
    """The documented subscription helper and the context fields are present."""
    assert callable(event_helper.async_track_state_change_event)
    context = Context()
    assert isinstance(context.id, str)
    assert context.id
    assert context.parent_id is None
    assert context.user_id is None
    child = Context(parent_id=context.id)
    assert child.parent_id == context.id
    assert child.id != context.id


# --- the causal chain -------------------------------------------------------


async def test_automation_chain_is_linked_by_context(
    hass: HomeAssistant, recorded: list[Event[Any]]
) -> None:
    """Trigger -> automation -> service call -> state change share context."""
    await _setup_booleans(hass)
    await _setup_automation(
        hass, [{"trigger": "state", "entity_id": MOTION, "to": "on"}]
    )
    recorded.clear()

    origin = Context()
    await hass.services.async_call(
        "input_boolean", "turn_on", {"entity_id": MOTION}, blocking=True, context=origin
    )
    await hass.async_block_till_done()

    motion = _only(recorded, EVENT_STATE_CHANGED, _state_change_of(MOTION))
    triggered = _only(recorded, EVENT_AUTOMATION_TRIGGERED, lambda _: True)
    # The shape of service_data is not stable (string or list of entity IDs),
    # so the target is only checked loosely here.
    action = _only(
        recorded, EVENT_CALL_SERVICE, lambda e: HALL in str(e.data["service_data"])
    )
    hall = _only(recorded, EVENT_STATE_CHANGED, _state_change_of(HALL))

    # The trigger carries the caller's context directly.
    assert motion.context.id == origin.id
    # The automation starts a NEW context whose parent is the trigger context.
    assert triggered.context.id != origin.id
    assert triggered.context.parent_id == origin.id
    assert triggered.data["entity_id"] == "automation.demo_hall_light"
    # The action and the resulting state change carry the automation context.
    assert action.context.id == triggered.context.id
    assert (action.data["domain"], action.data["service"]) == (
        "input_boolean",
        "turn_on",
    )
    assert hall.context.id == triggered.context.id
    assert hall.data["new_state"].context.id == triggered.context.id

    chain = [motion, triggered, action, hall]
    assert [recorded.index(e) for e in chain] == sorted(
        recorded.index(e) for e in chain
    )
    stamps = [e.time_fired_timestamp for e in chain]
    assert stamps == sorted(stamps)


async def test_documented_payload_fields(
    hass: HomeAssistant, recorded: list[Event[Any]]
) -> None:
    """Payloads contain exactly the documented keys.

    call_service exposes the full service_data. It can hold codes or passwords,
    so the normalizer must never keep it.
    """
    await _setup_booleans(hass)
    await _setup_automation(
        hass, [{"trigger": "state", "entity_id": MOTION, "to": "on"}]
    )
    recorded.clear()
    await hass.services.async_call(
        "input_boolean", "turn_on", {"entity_id": MOTION}, blocking=True
    )
    await hass.async_block_till_done()

    for event in recorded:
        if event.event_type == EVENT_STATE_CHANGED:
            assert set(event.data) == {"entity_id", "old_state", "new_state"}
        elif event.event_type == EVENT_CALL_SERVICE:
            assert set(event.data) == {"domain", "service", "service_data"}
        elif event.event_type == EVENT_AUTOMATION_TRIGGERED:
            assert {"name", "entity_id"} <= set(event.data)
            assert set(event.data) <= {"name", "entity_id", "source"}
    assert {e.event_type for e in recorded} == {
        EVENT_STATE_CHANGED,
        EVENT_CALL_SERVICE,
        EVENT_AUTOMATION_TRIGGERED,
    }


# --- limits: where context gives no evidence --------------------------------


async def test_state_change_without_context_has_no_parent(
    hass: HomeAssistant, recorded: list[Event[Any]]
) -> None:
    """A device-originated change gets a fresh context: no parent, no user."""
    hass.states.async_set("sensor.demo_device", "on")
    await hass.async_block_till_done()

    event = _only(recorded, EVENT_STATE_CHANGED, _state_change_of("sensor.demo_device"))
    assert event.context.parent_id is None
    assert event.context.user_id is None


async def test_direct_service_call_has_no_parent(
    hass: HomeAssistant, recorded: list[Event[Any]], hass_admin_user: MockUser
) -> None:
    """A direct (e.g. UI) action carries its own context without a parent."""
    await _setup_booleans(hass)
    recorded.clear()
    user_context = Context(user_id=hass_admin_user.id)
    await hass.services.async_call(
        "input_boolean",
        "turn_on",
        {"entity_id": HALL},
        blocking=True,
        context=user_context,
    )

    hall = _only(recorded, EVENT_STATE_CHANGED, _state_change_of(HALL))
    assert hall.context.id == user_context.id
    assert hall.context.parent_id is None
    # user_id is present on the event; the integration must never store it.
    assert hall.context.user_id == hass_admin_user.id


# The time pattern trigger keeps its next timer scheduled after the test.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_time_triggered_automation_has_no_parent(
    hass: HomeAssistant, recorded: list[Event[Any]]
) -> None:
    """Automations without a causing event start a root context.

    The pattern can also fire on the real clock at any time, so the action
    toggles: every run produces exactly one state change in its own context.
    """
    await _setup_booleans(hass)
    recorded.clear()
    await _setup_automation(
        hass, [{"trigger": "time_pattern", "seconds": "/1"}], service="toggle"
    )

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()

    triggered = [e for e in recorded if e.event_type == EVENT_AUTOMATION_TRIGGERED]
    hall = [
        e
        for e in recorded
        if e.event_type == EVENT_STATE_CHANGED and e.data["entity_id"] == HALL
    ]
    assert triggered
    assert all(e.context.parent_id is None for e in triggered)
    assert [e.context.id for e in hall] == [e.context.id for e in triggered]


async def test_script_reuses_caller_context(
    hass: HomeAssistant, recorded: list[Event[Any]]
) -> None:
    """Scripts do not open a new context: script_started has the caller's id."""
    await _setup_booleans(hass)
    assert await async_setup_component(
        hass,
        "script",
        {
            "script": {
                "demo_script": {
                    "sequence": [
                        {
                            "action": "input_boolean.turn_on",
                            "target": {"entity_id": HALL},
                        }
                    ]
                }
            }
        },
    )
    recorded.clear()
    caller = Context()
    await hass.services.async_call(
        "script", "demo_script", blocking=True, context=caller
    )
    await hass.async_block_till_done()

    started = _only(recorded, EVENT_SCRIPT_STARTED, lambda _: True)
    assert started.context.id == caller.id
    assert set(started.data) == {"name", "entity_id"}
    hall = _only(recorded, EVENT_STATE_CHANGED, _state_change_of(HALL))
    assert hall.context.id == caller.id


async def test_state_reported_carries_no_transition(hass: HomeAssistant) -> None:
    """state_reported only confirms an unchanged state; it is not used."""
    reported: list[Event[Any]] = []
    changed: list[Event[Any]] = []

    @callback
    def _filter(_data: Any) -> bool:  # noqa: ANN401 - HA event filter signature
        return True

    hass.bus.async_listen(EVENT_STATE_REPORTED, reported.append, event_filter=_filter)
    hass.bus.async_listen(EVENT_STATE_CHANGED, changed.append)

    hass.states.async_set("sensor.demo_temperature", "20")
    hass.states.async_set("sensor.demo_temperature", "20")
    await hass.async_block_till_done()

    assert len(changed) == 1
    assert len(reported) == 1
    assert "old_state" not in reported[0].data


def test_entity_context_expires_after_a_few_seconds() -> None:
    """An entity keeps a service context only briefly.

    A device that reports back later than this writes its state with a fresh
    context, so the change is UNRESOLVED instead of linked to the action.
    """
    assert entity_helper.CONTEXT_RECENT_TIME_SECONDS == 5
