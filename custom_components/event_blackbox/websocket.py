"""WebSocket commands for the panel.

All commands require an administrator. status and explain only read the
in-memory buffer, answer for allowlisted entities only and never send context
or user IDs. get_config and set_config read and change the options with the
same validation as the options flow.
"""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
import homeassistant.util.dt as dt_util
import probatio

from .const import DOMAIN
from .history import async_remove_history
from .options import (
    LIMITS,
    MAX_ALLOWLIST_ENTITIES,
    SELECTABLE_DOMAINS,
    SENSITIVE_DOMAINS,
    parse_options,
    validate_user_input,
)
from .query import build_overview, build_report
from .services import async_active_recorder

COMMAND_STATUS: Final = "event_blackbox/status"
COMMAND_EXPLAIN: Final = "event_blackbox/explain"
COMMAND_GET_CONFIG: Final = "event_blackbox/get_config"
COMMAND_SET_CONFIG: Final = "event_blackbox/set_config"
COMMAND_CLEAR: Final = "event_blackbox/clear"
ERROR_NOT_CONFIGURED: Final = "not_configured"
ERROR_NOT_RECORDING: Final = "not_recording"
ERROR_NOT_ON_ALLOWLIST: Final = "not_on_allowlist"


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register the commands once per Home Assistant start."""
    websocket_api.async_register_command(hass, ws_status)
    websocket_api.async_register_command(hass, ws_explain)
    websocket_api.async_register_command(hass, ws_get_config)
    websocket_api.async_register_command(hass, ws_set_config)
    websocket_api.async_register_command(hass, ws_clear)


@websocket_api.require_admin
@websocket_api.websocket_command({probatio.Required("type"): COMMAND_STATUS})
@callback
def ws_status(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Report recording state, buffer use and an overview for the monitor."""
    recorder = async_active_recorder(hass)
    if recorder is None:
        connection.send_result(
            msg["id"],
            {
                "recording": False,
                "started_at": None,
                "history_days": 0,
                "buffer": None,
                **build_overview(records=(), started_at=dt_util.utcnow()),
            },
        )
        return
    buffer = recorder.buffer
    records = buffer.records(now_ms=recorder.now_ms())
    stats = buffer.stats()
    connection.send_result(
        msg["id"],
        {
            "recording": True,
            "started_at": recorder.started_at.isoformat(),
            "history_days": _history_days(hass),
            "buffer": {
                "events": stats.events,
                "max_events": buffer.max_events,
                "bytes": stats.bytes,
                "max_bytes": buffer.max_bytes,
                "dropped": stats.dropped_count
                + stats.dropped_size
                + stats.dropped_age
                + stats.rejected,
            },
            **build_overview(records=records, started_at=recorder.started_at),
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        probatio.Required("type"): COMMAND_EXPLAIN,
        probatio.Required("entity_id"): cv.entity_id,
        probatio.Optional("event_id"): probatio.All(int, probatio.Range(min=1)),
    }
)
@callback
def ws_explain(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Explain a recorded change of an allowlisted entity."""
    recorder = async_active_recorder(hass)
    if recorder is None:
        connection.send_error(msg["id"], ERROR_NOT_RECORDING, "Recording is off")
        return
    entity_id: str = msg["entity_id"]
    if not recorder.allowed(entity_id):
        connection.send_error(
            msg["id"], ERROR_NOT_ON_ALLOWLIST, "Entity is not on the allowlist"
        )
        return
    connection.send_result(
        msg["id"],
        build_report(
            entity_id=entity_id,
            records=recorder.buffer.records(now_ms=recorder.now_ms()),
            started_at=recorder.started_at,
            event_id=msg.get("event_id"),
        ),
    )


@websocket_api.require_admin
@websocket_api.websocket_command({probatio.Required("type"): COMMAND_GET_CONFIG})
@callback
def ws_get_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the current options and everything the panel needs to edit them."""
    entries = hass.config_entries.async_entries(DOMAIN)
    connection.send_result(
        msg["id"],
        {
            "configured": bool(entries),
            "options": parse_options(entries[0].options).as_dict() if entries else None,
            "limits": {
                key: {
                    "min": limit.minimum,
                    "max": limit.maximum,
                    "default": limit.default,
                }
                for key, limit in LIMITS.items()
            },
            "max_entities": MAX_ALLOWLIST_ENTITIES,
            "sensitive_domains": sorted(SENSITIVE_DOMAINS),
            "selectable_domains": list(SELECTABLE_DOMAINS),
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        probatio.Required("type"): COMMAND_SET_CONFIG,
        probatio.Required("options"): dict,
        probatio.Optional("acknowledge_sensitive", default=False): bool,
    }
)
@callback
def ws_set_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Validate and save options; the config entry reloads with them."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], ERROR_NOT_CONFIGURED, "Not configured")
        return
    options, errors = validate_user_input(msg["options"])
    if options is None:
        connection.send_result(
            msg["id"], {"saved": False, "errors": errors, "sensitive_domains": []}
        )
        return
    sensitive = list(options.sensitive_domains)
    if sensitive and not msg["acknowledge_sensitive"]:
        connection.send_result(
            msg["id"],
            {
                "saved": False,
                "errors": {"base": "acknowledgement_required"},
                "sensitive_domains": sensitive,
            },
        )
        return
    hass.config_entries.async_update_entry(entries[0], options=options.as_dict())
    connection.send_result(
        msg["id"], {"saved": True, "errors": {}, "sensitive_domains": sensitive}
    )


def _history_days(hass: HomeAssistant) -> int:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if entry.runtime_data.history is not None:
            days: int = entry.runtime_data.options.history_days
            return days
    return 0


@websocket_api.require_admin
@websocket_api.websocket_command({probatio.Required("type"): COMMAND_CLEAR})
@websocket_api.async_response
async def ws_clear(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete everything recorded: the memory and the stored history."""
    cleared = False
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        data = entry.runtime_data
        if data.history is not None:
            await data.history.async_clear()
            cleared = True
        elif data.recorder is not None:
            data.recorder.buffer.clear()
            cleared = True
    if not cleared:
        await async_remove_history(hass)
    connection.send_result(msg["id"], {"cleared": True})
