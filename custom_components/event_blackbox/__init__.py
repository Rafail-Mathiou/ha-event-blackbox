"""The Event Blackbox integration.

Loading a config entry parses the options (opt-in, allowlist, limits). Only
when collection is active, the allowlisted events are recorded into a bounded
in-memory buffer (events.py). There is no storage, no service call and no
network access. Unload releases every listener and clears the buffer.
Administrators read the buffer through the action event_blackbox.explain
(services.py) or the timeline panel (panel.py, websocket.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    COLLECTION_STATUS_OFF,
    COLLECTION_STATUS_RECORDING,
    DOMAIN,
)
from .events import Recorder, async_subscribe
from .history import History, async_remove_history
from .options import BlackboxOptions, parse_options
from .panel import async_register_panel, async_register_static_path, async_remove_panel
from .services import async_setup_services
from .websocket import async_register_commands

# Set up through the UI only; there is no YAML configuration.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class EventBlackboxRuntimeData:
    """Runtime state of a loaded config entry. Lives in RAM only."""

    options: BlackboxOptions = field(default_factory=BlackboxOptions)
    recorder: Recorder | None = None
    history: History | None = None

    @property
    def collection_status(self) -> str:
        """Whether events are being recorded."""
        return (
            COLLECTION_STATUS_OFF
            if self.recorder is None
            else (COLLECTION_STATUS_RECORDING)
        )


type EventBlackboxConfigEntry = ConfigEntry[EventBlackboxRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the action, the WebSocket commands and the panel code."""
    async_setup_services(hass)
    async_register_commands(hass)
    await async_register_static_path(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: EventBlackboxConfigEntry
) -> bool:
    """Set up Event Blackbox from a config entry."""
    options = parse_options(entry.options)
    recorder: Recorder | None = None
    history: History | None = None
    if options.active:
        recorder = Recorder(options)
        if options.history:
            # Restore before subscribing, so saved records come first.
            history = History(hass, recorder)
            await history.async_load()
            recorder.on_record = history.schedule_save
        async_subscribe(hass, entry, recorder)
    if history is None:
        # History off (or collection off): no stored events may remain.
        await async_remove_history(hass)
    entry.runtime_data = EventBlackboxRuntimeData(
        options=options, recorder=recorder, history=history
    )
    integration = await async_get_integration(hass, DOMAIN)
    await async_register_panel(hass, str(integration.version))
    entry.async_on_unload(lambda: async_remove_panel(hass))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: EventBlackboxConfigEntry
) -> bool:
    """Unload a config entry.

    Listeners, the buffer and the panel are registered with
    entry.async_on_unload, so Home Assistant releases them and nothing stays
    behind. With history on, the buffer is saved first.
    """
    if entry.runtime_data.history is not None:
        await entry.runtime_data.history.async_save()
    return True


async def async_remove_entry(
    hass: HomeAssistant, entry: EventBlackboxConfigEntry
) -> None:
    """Delete the stored history when the integration is removed."""
    await async_remove_history(hass)


async def _async_update_listener(
    hass: HomeAssistant, entry: EventBlackboxConfigEntry
) -> None:
    """Reload the entry so changed options take effect."""
    await hass.config_entries.async_reload(entry.entry_id)
