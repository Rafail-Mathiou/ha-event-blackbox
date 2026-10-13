"""Security and privacy tests.

- Data minimisation: passwords, coordinates, camera attributes and made-up
  token payloads never appear in the report, logs or diagnostics.
- Lifecycle: no listener at first; opt-in starts, opt-out stops; reload and
  removal leave no listener and no data.
- Read only: no service call and no network access by the integration.
- Authorization: a non-admin request is rejected.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import patch

from homeassistant.core import Context, HomeAssistant, ServiceRegistry
from homeassistant.exceptions import Unauthorized
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.event_blackbox.const import DOMAIN
from custom_components.event_blackbox.events import Recorder
from custom_components.event_blackbox.services import SERVICE_EXPLAIN

from .conftest import bus_listeners

# Values that must never leave Home Assistant's own state machine. They are
# made up, look like real secrets and use documentation-only ranges.
FAKE_CODE = "synthetic-password-0000-not-real"
FAKE_BEARER = "synthetic.jwt-like.token-0000-not-real"
FAKE_LAT = "52.0000123"
FAKE_LON = "13.0000456"
FAKE_STREAM_URL = "rtsp://192.0.2.10/demo-stream"
FAKE_ZONE = "demo_secret_zone"
FAKE_VALUES = (
    FAKE_CODE,
    FAKE_BEARER,
    FAKE_LAT,
    FAKE_LON,
    FAKE_STREAM_URL,
    FAKE_ZONE,
)

ENTITIES = [
    "lock.demo_door",
    "camera.demo_porch",
    "person.demo_resident",
    "sensor.demo_body_temperature",
    "light.demo_hall",
]


def _options(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 - option values
    return {
        "collection_enabled": True,
        "entities": ENTITIES,
        "domains": [],
        "max_events": 2000,
        "max_kib": 2048,
        "max_age_minutes": 30,
        **overrides,
    }


async def _setup(hass: HomeAssistant, options: dict[str, Any]) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _recorder_of(entry: MockConfigEntry) -> Recorder | None:
    """Read the current recorder (a function, so type narrowing does not stick)."""
    recorder: Recorder | None = entry.runtime_data.recorder
    return recorder


def _write_sensitive_states(hass: HomeAssistant) -> None:
    """Simulate devices whose states and attributes carry secrets."""
    hass.states.async_set(
        "lock.demo_door", "unlocked", {"code": FAKE_CODE, "changed_by": "demo"}
    )
    hass.states.async_set(
        "camera.demo_porch",
        "streaming",
        {"access_token": FAKE_BEARER, "entity_picture": FAKE_STREAM_URL},
    )
    hass.states.async_set(
        "person.demo_resident",
        FAKE_ZONE,
        {"latitude": float(FAKE_LAT), "longitude": float(FAKE_LON)},
    )
    hass.states.async_set("sensor.demo_body_temperature", "36.6")
    hass.states.async_set("sensor.demo_body_temperature", "37.9")
    hass.states.async_set("light.demo_hall", FAKE_BEARER)


# --- data minimisation -------------------------------------------------------


async def test_secrets_never_reach_records_report_logs_or_diagnostics(
    hass: HomeAssistant,
    hass_admin_user: MockUser,
    hass_client: ClientSessionGenerator,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Secret attributes, coordinates, tokens and health values stay out."""
    caplog.set_level(logging.DEBUG)
    entry = await _setup(hass, _options())
    _write_sensitive_states(hass)
    hass.bus.async_fire(
        "call_service",
        {
            "domain": "light",
            "service": "turn_on",
            "service_data": {
                "entity_id": "light.demo_hall",
                "password": FAKE_CODE,
                "token": FAKE_BEARER,
            },
        },
    )
    await hass.async_block_till_done()

    recorder = entry.runtime_data.recorder
    assert isinstance(recorder, Recorder)
    records = json.dumps([r.as_dict() for r in recorder.buffer.records()])

    reports = [
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPLAIN,
            {"entity_id": entity_id},
            blocking=True,
            return_response=True,
            context=Context(user_id=hass_admin_user.id),
        )
        for entity_id in ENTITIES
    ]
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    integration_logs = "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("custom_components.event_blackbox")
    )

    plain = (*FAKE_VALUES, "unlocked", "streaming")
    for output in (records, json.dumps(diagnostics), integration_logs):
        for secret in (*plain, "36.6", "37.9"):
            assert secret not in output, secret
    # Reports contain wall clock times, so only non-numeric secrets are checked.
    for secret in plain:
        assert secret not in json.dumps(reports), secret
    # The changes themselves are still visible, reduced to their minimum.
    transitions: dict[str, Any] = {}
    for report in reports:
        assert report is not None
        change = report["change"]
        assert isinstance(change, dict)
        transitions[str(report["entity_id"])] = change["transition"]
    assert transitions == {
        # First states of sensitive entities: only that they appeared.
        "lock.demo_door": "appeared",
        "camera.demo_porch": "appeared",
        "person.demo_resident": "appeared",
        "sensor.demo_body_temperature": "value_to_value",
        "light.demo_hall": "none_to_other",
    }


async def test_oversized_and_hostile_values_are_bounded(hass: HomeAssistant) -> None:
    """Huge or markup-laden states and attributes leave no trace in records."""
    entry = await _setup(hass, _options(entities=["sensor.demo_value"]))
    hass.states.async_set(
        "sensor.demo_value",
        "<img src=x onerror=alert(1)>",
        {"payload": "x" * 1_000_000},
    )
    await hass.async_block_till_done()

    recorder = entry.runtime_data.recorder
    assert isinstance(recorder, Recorder)
    (record,) = recorder.buffer.records()
    assert record.transition == "none_to_other"
    assert len(json.dumps(record.as_dict())) < 500


# --- lifecycle ---------------------------------------------------------------


async def test_lifecycle_leaves_no_listener_and_no_data(hass: HomeAssistant) -> None:
    """Off, on, reload, off again and removal: listeners and data follow."""
    baseline = bus_listeners(hass)
    entry = await _setup(hass, _options(collection_enabled=False))
    assert bus_listeners(hass) == baseline
    assert _recorder_of(entry) is None

    hass.config_entries.async_update_entry(entry, options=_options())
    await hass.async_block_till_done()
    first = _recorder_of(entry)
    assert isinstance(first, Recorder)
    assert bus_listeners(hass) != baseline
    hass.states.async_set("light.demo_hall", "on")
    await hass.async_block_till_done()
    assert first.buffer.records()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert first.buffer.records() == ()
    second = _recorder_of(entry)
    assert isinstance(second, Recorder)
    assert second is not first
    assert second.buffer.records() == ()

    hass.config_entries.async_update_entry(
        entry, options=_options(collection_enabled=False)
    )
    await hass.async_block_till_done()
    assert bus_listeners(hass) == baseline
    assert second.buffer.records() == ()

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert bus_listeners(hass) == baseline
    assert hass.config_entries.async_entries(DOMAIN) == []


# --- read only ---------------------------------------------------------------


async def test_integration_never_calls_services_or_the_network(
    hass: HomeAssistant, hass_admin_user: MockUser
) -> None:
    """Recording and explaining make no service call and open no connection."""
    original = ServiceRegistry.async_call
    nested: list[tuple[str, str]] = []

    async def _spy(
        self: ServiceRegistry,
        domain: str,
        service: str,
        *args: Any,  # noqa: ANN401 - passed through unchanged
        **kwargs: Any,  # noqa: ANN401 - passed through unchanged
    ) -> Any:  # noqa: ANN401 - passed through unchanged
        nested.append((domain, service))
        return await original(self, domain, service, *args, **kwargs)

    with (
        patch.object(ServiceRegistry, "async_call", _spy),
        patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession"
        ) as session,
        patch("homeassistant.helpers.httpx_client.get_async_client") as httpx_client,
    ):
        entry = await _setup(hass, _options())
        _write_sensitive_states(hass)
        await hass.async_block_till_done()
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPLAIN,
            {"entity_id": "light.demo_hall"},
            blocking=True,
            return_response=True,
            context=Context(user_id=hass_admin_user.id),
        )
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert nested == [(DOMAIN, SERVICE_EXPLAIN)]
    session.assert_not_called()
    httpx_client.assert_not_called()


# --- authorization -----------------------------------------------------------


async def test_non_admin_request_is_rejected(
    hass: HomeAssistant, hass_read_only_user: MockUser
) -> None:
    """Users without administrator rights cannot read reports."""
    await _setup(hass, _options())
    hass.states.async_set("light.demo_hall", "on")
    await hass.async_block_till_done()
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_EXPLAIN,
            {"entity_id": "light.demo_hall"},
            blocking=True,
            return_response=True,
            context=Context(user_id=hass_read_only_user.id),
        )
