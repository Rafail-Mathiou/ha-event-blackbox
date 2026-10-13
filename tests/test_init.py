"""Lifecycle tests: setup is passive, unload and removal leave no trace."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.event_blackbox import EventBlackboxRuntimeData
from custom_components.event_blackbox.const import (
    DOMAIN,
)

from .conftest import SYNTHETIC_SECRETS, bus_listeners


def _state(entry: MockConfigEntry) -> ConfigEntryState:
    """Return the entry state (helper keeps type narrowing out of the way)."""
    state: ConfigEntryState = entry.state
    return state


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The entry loads without recording and unloads cleanly."""
    await _setup(hass, mock_config_entry)
    assert _state(mock_config_entry) is ConfigEntryState.LOADED
    assert isinstance(mock_config_entry.runtime_data, EventBlackboxRuntimeData)
    assert mock_config_entry.runtime_data.recorder is None

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert _state(mock_config_entry) is ConfigEntryState.NOT_LOADED
    assert mock_config_entry.update_listeners == []


async def test_setup_is_passive(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
    service_call_spy: list[tuple[str, str]],
) -> None:
    """Without opt-in: no listeners, no entities, no services, no storage, no IO."""
    # Home Assistant's own components the integration depends on keep their own
    # settings; set them up first so only Event Blackbox is measured.
    for dependency in ("frontend", "panel_custom", "websocket_api"):
        assert await async_setup_component(hass, dependency, {})
    await hass.async_block_till_done()
    listeners_before = bus_listeners(hass)
    states_before = hass.states.async_entity_ids()

    with (
        patch(
            "homeassistant.helpers.storage.Store.async_save", new_callable=AsyncMock
        ) as store_save,
        patch(
            "homeassistant.helpers.storage.Store.async_delay_save"
        ) as store_delay_save,
        patch(
            "homeassistant.helpers.aiohttp_client.async_get_clientsession"
        ) as client_session,
    ):
        await _setup(hass, mock_config_entry)

    assert bus_listeners(hass) == listeners_before
    assert hass.states.async_entity_ids() == states_before
    # Only the read-only, admin-only explain action exists.
    assert set(hass.services.async_services_for_domain(DOMAIN)) == {"explain"}
    assert service_call_spy == []
    store_save.assert_not_called()
    store_delay_save.assert_not_called()
    client_session.assert_not_called()
    assert not [key for key in hass_storage if DOMAIN in key]


async def test_options_update_reloads_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Changed options take effect through a reload."""
    await _setup(hass, mock_config_entry)
    with patch.object(
        hass.config_entries, "async_reload", new_callable=AsyncMock
    ) as reload:
        hass.config_entries.async_update_entry(mock_config_entry, options={"probe": 1})
        await hass.async_block_till_done()
    reload.assert_awaited_once_with(mock_config_entry.entry_id)


async def test_unknown_entry_data_is_ignored(hass: HomeAssistant) -> None:
    """Unexpected stored data does not activate anything and fails safe."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=SYNTHETIC_SECRETS, options=SYNTHETIC_SECRETS
    )
    listeners_before = bus_listeners(hass)
    await _setup(hass, entry)
    assert _state(entry) is ConfigEntryState.LOADED
    assert entry.runtime_data.recorder is None
    assert bus_listeners(hass) == listeners_before


async def test_remove_entry_leaves_no_trace(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Removing the integration removes listeners and stored data."""
    listeners_before = bus_listeners(hass)
    await _setup(hass, mock_config_entry)
    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.config_entries.async_entries(DOMAIN) == []
    assert bus_listeners(hass) == listeners_before
    assert not [key for key in hass_storage if DOMAIN in key]


async def test_allowlist_without_opt_in_stays_inactive(hass: HomeAssistant) -> None:
    """Without opt-in, a stored allowlist activates nothing (0 listeners)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            "collection_enabled": False,
            "entities": ["light.demo_hall"],
            "domains": ["binary_sensor"],
            "max_events": 2000,
            "max_kib": 2048,
            "max_age_minutes": 30,
        },
    )
    listeners_before = bus_listeners(hass)
    await _setup(hass, entry)
    assert entry.runtime_data.options.active is False
    assert bus_listeners(hass) == listeners_before
