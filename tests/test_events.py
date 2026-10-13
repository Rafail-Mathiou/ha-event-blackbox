"""Recording of allowlisted events into the bounded buffer."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Context, HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.event_blackbox.const import (
    COLLECTION_STATUS_OFF,
    COLLECTION_STATUS_RECORDING,
    DOMAIN,
)
from custom_components.event_blackbox.correlation import CausalGraph, Evidence, Note
from custom_components.event_blackbox.events import Recorder
from custom_components.event_blackbox.normalizer import EventKind, EventRecord

from .conftest import bus_listeners

MOTION = "input_boolean.demo_motion"
HALL = "input_boolean.demo_hall"
OTHER = "input_boolean.demo_other"


def _options(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 - option values
    return {
        "collection_enabled": True,
        "entities": [MOTION, HALL],
        "domains": [],
        "max_events": 2000,
        "max_kib": 2048,
        "max_age_minutes": 30,
        **overrides,
    }


def _recorder(entry: MockConfigEntry) -> Recorder:
    recorder = entry.runtime_data.recorder
    assert isinstance(recorder, Recorder)
    return recorder


def _records(entry: MockConfigEntry) -> tuple[EventRecord, ...]:
    return _recorder(entry).buffer.records()


async def _setup_booleans(hass: HomeAssistant) -> None:
    assert await async_setup_component(
        hass,
        "input_boolean",
        {"input_boolean": {"demo_motion": {}, "demo_hall": {}, "demo_other": {}}},
    )
    await hass.async_block_till_done()


async def _setup_entry(hass: HomeAssistant, options: dict[str, Any]) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _turn_on(
    hass: HomeAssistant, entity_id: str, context: Context | None = None
) -> None:
    await hass.services.async_call(
        "input_boolean",
        "turn_on",
        {"entity_id": entity_id},
        blocking=True,
        context=context,
    )
    await hass.async_block_till_done()


async def test_active_options_start_recording(hass: HomeAssistant) -> None:
    """Opt-in with an allowlist registers listeners and records state changes."""
    await _setup_booleans(hass)
    before = bus_listeners(hass)
    entry = await _setup_entry(hass, _options())

    assert entry.runtime_data.collection_status == COLLECTION_STATUS_RECORDING
    assert bus_listeners(hass) != before

    await _turn_on(hass, HALL)
    states = [r for r in _records(entry) if r.kind is EventKind.STATE_CHANGE]
    assert [(r.entity_ref, r.transition) for r in states] == [(HALL, "off_to_on")]


async def test_entities_outside_the_allowlist_are_not_recorded(
    hass: HomeAssistant,
) -> None:
    """Changes and service calls of other entities leave no record."""
    await _setup_booleans(hass)
    entry = await _setup_entry(hass, _options())

    await _turn_on(hass, OTHER)
    hass.states.async_set("light.demo_unlisted", "on")
    await hass.async_block_till_done()

    assert _records(entry) == ()
    assert _recorder(entry).ignored >= 1  # the service call to OTHER


async def test_domain_allowlist(hass: HomeAssistant) -> None:
    """A domain on the allowlist records all of its entities once."""
    entry = await _setup_entry(
        hass, _options(entities=["binary_sensor.demo_door"], domains=["binary_sensor"])
    )
    hass.states.async_set("binary_sensor.demo_motion", "on")
    hass.states.async_set("binary_sensor.demo_door", "on")
    hass.states.async_set("sensor.demo_value", "1")
    await hass.async_block_till_done()

    refs = [r.entity_ref for r in _records(entry)]
    assert refs == ["binary_sensor.demo_motion", "binary_sensor.demo_door"]


async def test_automation_chain_is_recorded_with_contexts(hass: HomeAssistant) -> None:
    """Trigger, automation, service call and effect are recorded and linked."""
    await _setup_booleans(hass)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "id": "demo_hall_light",
                "alias": "Demo hall light",
                "triggers": [{"trigger": "state", "entity_id": MOTION, "to": "on"}],
                "actions": [
                    {"action": "input_boolean.turn_on", "target": {"entity_id": HALL}}
                ],
            }
        },
    )
    await hass.async_block_till_done()
    entry = await _setup_entry(hass, _options())

    origin = Context()
    await _turn_on(hass, MOTION, origin)

    records = _records(entry)
    kinds = [(r.kind, r.entity_ref or r.service) for r in records]
    assert kinds == [
        (EventKind.SERVICE_CALL, "input_boolean.turn_on"),
        (EventKind.STATE_CHANGE, MOTION),
        (EventKind.AUTOMATION_TRIGGERED, "automation.demo_hall_light"),
        (EventKind.SERVICE_CALL, "input_boolean.turn_on"),
        (EventKind.STATE_CHANGE, HALL),
    ]
    _call, motion, run, action, hall = records
    assert motion.context_ref == origin.id
    assert run.parent_context_ref == origin.id
    assert action.context_ref == run.context_ref
    assert action.target_refs == (HALL,)
    assert hall.context_ref == run.context_ref
    assert [r.event_id for r in records] == sorted(r.event_id for r in records)

    # On real records: the whole chain is confirmed by context.
    explanation = CausalGraph(records).explain(hall.event_id)
    assert explanation is not None
    assert explanation.evidence is Evidence.CONFIRMED_BY_CONTEXT
    assert [(link.cause.kind, link.effect.kind) for link in explanation.chain] == [
        (EventKind.SERVICE_CALL, EventKind.STATE_CHANGE),
        (EventKind.STATE_CHANGE, EventKind.AUTOMATION_TRIGGERED),
        (EventKind.AUTOMATION_TRIGGERED, EventKind.SERVICE_CALL),
        (EventKind.SERVICE_CALL, EventKind.STATE_CHANGE),
    ]
    assert Note.DIRECT_ACTION in explanation.notes


async def test_script_run_is_recorded(hass: HomeAssistant) -> None:
    """A script run is kept with its entity and the caller's context."""
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
    await hass.async_block_till_done()
    entry = await _setup_entry(hass, _options())
    caller = Context()
    await hass.services.async_call(
        "script", "demo_script", blocking=True, context=caller
    )
    await hass.async_block_till_done()

    scripts = [r for r in _records(entry) if r.kind is EventKind.SCRIPT_STARTED]
    assert [(r.entity_ref, r.context_ref) for r in scripts] == [
        ("script.demo_script", caller.id)
    ]


async def test_unload_releases_listeners_and_clears_buffer(
    hass: HomeAssistant,
) -> None:
    """After unload no listener and no record remains."""
    await _setup_booleans(hass)
    before = bus_listeners(hass)
    entry = await _setup_entry(hass, _options())
    await _turn_on(hass, HALL)
    recorder = _recorder(entry)
    assert recorder.buffer.stats().events > 0

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert bus_listeners(hass) == before
    assert recorder.buffer.records() == ()


async def test_opt_out_stops_recording(hass: HomeAssistant) -> None:
    """Turning collection off reloads the entry without any listener."""
    await _setup_booleans(hass)
    before = bus_listeners(hass)
    entry = await _setup_entry(hass, _options())

    hass.config_entries.async_update_entry(
        entry, options=_options(collection_enabled=False)
    )
    await hass.async_block_till_done()

    assert entry.runtime_data.recorder is None
    assert entry.runtime_data.collection_status == COLLECTION_STATUS_OFF
    assert bus_listeners(hass) == before


async def test_remove_entry_leaves_no_listener(hass: HomeAssistant) -> None:
    """Removing the integration releases everything."""
    await _setup_booleans(hass)
    before = bus_listeners(hass)
    entry = await _setup_entry(hass, _options(entities=[], domains=["light"]))
    hass.states.async_set("light.demo_desk", "on")
    await hass.async_block_till_done()
    assert [r.entity_ref for r in _records(entry)] == ["light.demo_desk"]

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert bus_listeners(hass) == before


async def test_malformed_payload_is_counted_not_raised(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken event is counted as an error, nothing is logged or stored."""
    entry = await _setup_entry(hass, _options(entities=["light.demo_hall"]))
    caplog.set_level(logging.DEBUG)

    hass.bus.async_fire(
        str(EVENT_STATE_CHANGED),
        {"entity_id": "light.demo_hall", "old_state": None, "new_state": "broken"},
    )
    hass.bus.async_fire("automation_triggered", {"name": "demo"})
    hass.bus.async_fire("script_started", {})
    hass.bus.async_fire("call_service", {"domain": "light"})
    await hass.async_block_till_done()

    recorder = _recorder(entry)
    assert recorder.errors == 4
    assert _records(entry) == ()
    assert "demo_hall" not in caplog.text


async def test_recording_logs_nothing_about_events(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Even at debug level, no entity or context ID reaches the log."""
    await _setup_booleans(hass)
    caplog.set_level(logging.DEBUG, logger="custom_components.event_blackbox")
    entry = await _setup_entry(hass, _options())
    context = Context()
    await _turn_on(hass, HALL, context)

    assert _records(entry)
    integration_logs = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("custom_components.event_blackbox")
    ]
    assert not any("demo_hall" in m or context.id in m for m in integration_logs)


async def test_diagnostics_show_counters_only(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    """Diagnostics contain buffer counters, never records or contexts."""
    await _setup_booleans(hass)
    entry = await _setup_entry(hass, _options())
    context = Context()
    await _turn_on(hass, HALL, context)

    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    dumped = json.dumps(result)

    buffer = result["buffer"]
    assert isinstance(buffer, dict)
    assert result["collection_status"] == COLLECTION_STATUS_RECORDING
    assert buffer["events"] == len(_records(entry))
    assert set(buffer) == {
        "events",
        "bytes",
        "received",
        "rejected",
        "dropped_count",
        "dropped_size",
        "dropped_age",
        "ignored",
        "errors",
    }
    for leak in ("demo_hall", "demo_motion", context.id, "off_to_on", "turn_on"):
        assert leak not in dumped


async def test_hundred_thousand_events_keep_limits_and_loop_responsive(
    hass: HomeAssistant,
) -> None:
    """Load test through the real event bus: limits hold, callbacks stay cheap."""
    entry = await _setup_entry(
        hass, _options(entities=["light.demo_hall"], max_events=2000, max_kib=256)
    )
    recorder = _recorder(entry)
    started = time.perf_counter()
    for i in range(100_000):
        hass.states.async_set("light.demo_hall", "on" if i % 2 else "off")
        if i % 10_000 == 0:
            await hass.async_block_till_done()
            stats = recorder.buffer.stats()
            assert stats.events <= 2000
            assert stats.bytes <= 256 * 1024
    await hass.async_block_till_done()
    elapsed = time.perf_counter() - started

    stats = recorder.buffer.stats()
    assert stats.received == 100_000
    assert stats.events <= 2000
    assert stats.bytes <= 256 * 1024
    assert stats.dropped_count + stats.dropped_size >= 98_000
    # Home Assistant itself does most of the work here; the budget covers
    # state writes, event dispatch and recording on a CI runner.
    assert elapsed < 60
