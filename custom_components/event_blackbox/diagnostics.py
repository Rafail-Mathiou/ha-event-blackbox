"""Diagnostics for Event Blackbox.

Diagnostics files leave the household (support requests, forum posts). They
therefore contain only an allowlist of aggregated, non-identifying values:
never config entry data or option values, entity IDs, context IDs, names,
timestamps, payloads or anything derived from user data.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from . import EventBlackboxConfigEntry
from .const import DATA_SCHEMA_VERSION, DOMAIN
from .events import Recorder


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EventBlackboxConfigEntry
) -> dict[str, Any]:
    """Return privacy-safe diagnostics for a config entry."""
    integration = await async_get_integration(hass, DOMAIN)
    options = entry.runtime_data.options
    return {
        "integration_version": str(integration.version),
        "config_entry_version": f"{entry.version}.{entry.minor_version}",
        "data_schema_version": DATA_SCHEMA_VERSION,
        "option_count": len(entry.options),
        # Counts only - never the selected entity IDs or domains.
        "collection_enabled": options.collection_enabled,
        "allowlist_entity_count": len(options.entities),
        "allowlist_domain_count": len(options.domains),
        "sensitive_domain_count": len(options.sensitive_domains),
        "collection_status": entry.runtime_data.collection_status,
        "history_days": options.history_days if entry.runtime_data.history else 0,
        "buffer": _buffer_counters(entry.runtime_data.recorder),
    }


def _buffer_counters(recorder: Recorder | None) -> dict[str, int] | None:
    """Return buffer counters. Never records, entity IDs, contexts or times."""
    if recorder is None:
        return None
    stats = recorder.buffer.stats()
    return {
        "events": stats.events,
        "bytes": stats.bytes,
        "received": stats.received,
        "rejected": stats.rejected,
        "dropped_count": stats.dropped_count,
        "dropped_size": stats.dropped_size,
        "dropped_age": stats.dropped_age,
        "ignored": recorder.ignored,
        "errors": recorder.errors,
    }
