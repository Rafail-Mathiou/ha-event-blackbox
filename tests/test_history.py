"""Optional history across restarts."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any
from unittest.mock import patch

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.setup import async_setup_component
import homeassistant.util.dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import (
    ClientSessionGenerator,
    WebSocketGenerator,
)

from custom_components.event_blackbox.const import DOMAIN
from custom_components.event_blackbox.events import Recorder
from custom_components.event_blackbox.history import STORAGE_KEY

HALL = "input_boolean.demo_hall"


def _options(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 - option values
    return {
        "collection_enabled": True,
        "entities": [HALL],
        "domains": [],
        "max_events": 2000,
        "max_kib": 2048,
        "max_age_minutes": 30,
        "history_days": 3,
        **overrides,
    }


def _stored(wall: str, seq: int = 1, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "schema": 1,
        "event_id": seq,
        "relative_ms": 0,
        "kind": "state_change",
        "entity_ref": HALL,
        "transition": "off_to_on",
        "context_ref": f"CTX_{seq}",
        "parent_context_ref": None,
        "service": None,
        "target_refs": [],
        "wall": wall,
        **overrides,
    }


def _file(records: Any) -> dict[str, Any]:  # noqa: ANN401 - stored structure
    return {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"records": records},
    }


async def _setup(hass: HomeAssistant, options: dict[str, Any]) -> MockConfigEntry:
    assert await async_setup_component(
        hass, "input_boolean", {"input_boolean": {"demo_hall": {}}}
    )
    entry = MockConfigEntry(domain=DOMAIN, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _recorder(entry: MockConfigEntry) -> Recorder:
    recorder = entry.runtime_data.recorder
    assert isinstance(recorder, Recorder)
    return recorder


async def _toggle(hass: HomeAssistant, service: str = "turn_on") -> None:
    await hass.services.async_call(
        "input_boolean", service, {"entity_id": HALL}, blocking=True, context=Context()
    )
    await hass.async_block_till_done()


async def test_history_is_off_by_default_and_old_file_is_removed(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Without the option nothing is written and a leftover file is deleted."""
    hass_storage[STORAGE_KEY] = _file([_stored(dt_util.utcnow().isoformat())])
    entry = await _setup(hass, _options(history_days=0))
    assert entry.runtime_data.history is None
    await _toggle(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert STORAGE_KEY not in hass_storage


async def test_history_is_saved_with_only_contract_fields(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Saved records hold the reduced contract fields and a wall clock time."""
    entry = await _setup(hass, _options())
    await _toggle(hass)
    hass.bus.async_fire(
        "call_service",
        {
            "domain": "input_boolean",
            "service": "turn_off",
            "service_data": {
                "entity_id": HALL,
                "password": "synthetic-password-not-real",
            },
        },
    )
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    records = hass_storage[STORAGE_KEY]["data"]["records"]
    assert records
    assert all(
        set(record)
        == {
            "schema",
            "event_id",
            "relative_ms",
            "kind",
            "entity_ref",
            "transition",
            "context_ref",
            "parent_context_ref",
            "service",
            "target_refs",
            "wall",
        }
        for record in records
    )
    assert "synthetic-password" not in json.dumps(records)


async def test_saving_is_batched(hass: HomeAssistant) -> None:
    """Many events cause one delayed write, not one write per event."""
    keys: list[str] = []
    original = Store.async_delay_save

    def _spy(self: Store[Any], *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        keys.append(self.key)
        original(self, *args, **kwargs)

    with patch.object(Store, "async_delay_save", _spy):
        await _setup(hass, _options())
        for service in ("turn_on", "turn_off", "turn_on", "turn_off"):
            await _toggle(hass, service)
    assert keys.count(STORAGE_KEY) == 1


async def test_delayed_save_writes_and_rearms(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """After the delay the file is written; the next event schedules again."""
    entry = await _setup(hass, _options())
    await _toggle(hass)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert len(hass_storage[STORAGE_KEY]["data"]["records"]) >= 1
    keys: list[str] = []
    original = Store.async_delay_save

    def _spy(self: Store[Any], *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        keys.append(self.key)
        original(self, *args, **kwargs)

    with patch.object(Store, "async_delay_save", _spy):
        await _toggle(hass, "turn_off")
    assert keys.count(STORAGE_KEY) == 1
    assert entry.runtime_data.history is not None


async def test_history_is_restored_after_a_restart(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Saved records come back in order, before live ones, within the retention."""
    now = dt_util.utcnow()
    hass_storage[STORAGE_KEY] = _file(
        [
            _stored((now - timedelta(days=4)).isoformat(), 1),  # older than 3 days
            _stored((now - timedelta(hours=5)).isoformat(), 2, transition="on_to_off"),
            _stored((now - timedelta(hours=2)).isoformat(), 3),
            _stored((now + timedelta(hours=1)).isoformat(), 4),  # in the future
            _stored("not a time", 5),
            _stored((now - timedelta(hours=1)).isoformat(), 6, kind="nonsense"),
            "not a record",
        ]
    )
    entry = await _setup(hass, _options())
    records = _recorder(entry).buffer.records()
    assert [r.transition for r in records] == ["on_to_off", "off_to_on"]
    assert [r.event_id for r in records] == [1, 2]
    assert all(r.relative_ms < 0 for r in records)

    await _toggle(hass, "turn_off")
    records = _recorder(entry).buffer.records()
    assert records[-1].event_id > 2
    assert records[-1].relative_ms >= 0


@pytest.mark.parametrize("data", [{"records": "nope"}, {"other": 1}, ["list"]])
async def test_manipulated_file_is_discarded(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    data: Any,  # noqa: ANN401
) -> None:
    """A file with an unexpected structure is removed and nothing is restored."""
    hass_storage[STORAGE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": data,
    }
    entry = await _setup(hass, _options())
    assert _recorder(entry).buffer.records() == ()
    assert STORAGE_KEY not in hass_storage


async def test_turning_history_off_deletes_the_file(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Opting out of history removes the stored file on reload."""
    entry = await _setup(hass, _options())
    await _toggle(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert STORAGE_KEY in hass_storage

    hass.config_entries.async_update_entry(entry, options=_options(history_days=0))
    await hass.async_block_till_done()
    assert STORAGE_KEY not in hass_storage


async def test_removing_the_integration_deletes_the_file(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Removal leaves no stored history behind."""
    entry = await _setup(hass, _options())
    await _toggle(hass)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert STORAGE_KEY not in hass_storage


async def test_clear_command_deletes_memory_and_file(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Administrators can delete everything recorded at once."""
    entry = await _setup(hass, _options())
    await _toggle(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert STORAGE_KEY in hass_storage
    assert _recorder(entry).buffer.records()

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "event_blackbox/clear"})
    response = await client.receive_json()
    assert response["result"] == {"cleared": True}
    assert _recorder(entry).buffer.records() == ()
    assert STORAGE_KEY not in hass_storage


async def test_clear_without_history_empties_memory(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Clearing also works when only memory is used."""
    entry = await _setup(hass, _options(history_days=0))
    await _toggle(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "event_blackbox/clear"})
    assert (await client.receive_json())["result"] == {"cleared": True}
    assert _recorder(entry).buffer.records() == ()


async def test_clear_without_recording_removes_a_leftover_file(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    hass_ws_client: WebSocketGenerator,
) -> None:
    """With nothing loaded, the command still deletes a stored file."""
    assert await async_setup_component(hass, DOMAIN, {})
    hass_storage[STORAGE_KEY] = _file([])
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "event_blackbox/clear"})
    assert (await client.receive_json())["result"] == {"cleared": True}
    assert STORAGE_KEY not in hass_storage


async def test_clear_rejects_non_admin(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_read_only_access_token: str,
) -> None:
    """Only administrators can delete recorded data."""
    entry = await _setup(hass, _options())
    await _toggle(hass)
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json_auto_id({"type": "event_blackbox/clear"})
    response = await client.receive_json()
    assert response["error"]["code"] == "unauthorized"
    assert _recorder(entry).buffer.records()


async def test_history_shows_in_status_and_diagnostics(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    hass_client: ClientSessionGenerator,
) -> None:
    """The panel and diagnostics show the number of days, nothing more."""
    entry = await _setup(hass, _options())
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "event_blackbox/status"})
    assert (await client.receive_json())["result"]["history_days"] == 3
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert diagnostics["history_days"] == 3


async def test_history_needs_collection(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """History alone does not record anything and keeps no file."""
    hass_storage[STORAGE_KEY] = _file([])
    entry = await _setup(hass, _options(collection_enabled=False))
    assert entry.runtime_data.recorder is None
    assert entry.runtime_data.history is None
    assert STORAGE_KEY not in hass_storage


async def test_clear_with_loaded_entry_without_recording(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    hass_ws_client: WebSocketGenerator,
) -> None:
    """A loaded entry that does not record still gets a leftover file deleted."""
    await _setup(hass, _options(collection_enabled=False))
    hass_storage[STORAGE_KEY] = _file([])
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "event_blackbox/clear"})
    assert (await client.receive_json())["result"] == {"cleared": True}
    assert STORAGE_KEY not in hass_storage
