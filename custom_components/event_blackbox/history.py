"""Optional history across restarts.

The only module that writes to Home Assistant's storage. It saves the reduced
records of the buffer, at most once a minute and on unload, into
.storage/event_blackbox.history, and deletes that file whenever history is
off, the integration is removed or an administrator asks for it.
"""

from __future__ import annotations

from typing import Any, Final

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .events import Recorder

STORAGE_KEY: Final = f"{DOMAIN}.history"
STORAGE_VERSION: Final = 1
SAVE_DELAY_SECONDS: Final = 60


def _store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    return Store(hass, STORAGE_VERSION, STORAGE_KEY, private=True)


class History:
    """Saves and restores the recorder's buffer."""

    def __init__(self, hass: HomeAssistant, recorder: Recorder) -> None:
        """Bind the history file to a recorder."""
        self._store = _store(hass)
        self._recorder = recorder
        self._pending = False

    async def async_load(self) -> int:
        """Restore saved records into the recorder. Returns how many were kept.

        A file with an unexpected structure is discarded and removed.
        """
        data = await self._store.async_load()
        if data is None:
            return 0
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list):
            await self._store.async_remove()
            return 0
        return self._recorder.restore(records)

    @callback
    def schedule_save(self) -> None:
        """Save soon; repeated calls within the delay cause one write."""
        if self._pending:
            return
        self._pending = True
        self._store.async_delay_save(self._data, SAVE_DELAY_SECONDS)

    async def async_save(self) -> None:
        """Save now, for example on unload."""
        await self._store.async_save(self._data())

    async def async_clear(self) -> None:
        """Forget everything: memory and file."""
        self._recorder.buffer.clear()
        self._pending = False
        await self._store.async_remove()

    @callback
    def _data(self) -> dict[str, Any]:
        self._pending = False
        return {"records": self._recorder.stored()}


async def async_remove_history(hass: HomeAssistant) -> None:
    """Delete the history file if it exists."""
    await _store(hass).async_remove()
