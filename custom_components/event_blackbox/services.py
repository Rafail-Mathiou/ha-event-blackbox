"""The read-only action event_blackbox.explain.

Registered once per Home Assistant start through the documented admin helper:
callers without administrator rights are rejected before the handler runs.
The handler only reads the in-memory buffer; it calls no other service, opens
no connection and stores nothing.
"""

from __future__ import annotations

from typing import Final

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
import probatio

from .const import DOMAIN
from .events import Recorder
from .query import build_report

SERVICE_EXPLAIN: Final = "explain"
EXPLAIN_SCHEMA: Final = probatio.Schema(
    {probatio.Required(ATTR_ENTITY_ID): cv.entity_id}
)


@callback
def async_active_recorder(hass: HomeAssistant) -> Recorder | None:
    """Return the recorder of the loaded entry, if collection is active."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        recorder = entry.runtime_data.recorder
        if isinstance(recorder, Recorder):
            return recorder
    return None


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the admin-only explain action."""

    # A coroutine on purpose: the admin helper discards the return value of
    # plain callbacks, and the report is the response of this action.
    async def _explain(call: ServiceCall) -> ServiceResponse:
        recorder = async_active_recorder(hass)
        if recorder is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_recording"
            )
        entity_id: str = call.data[ATTR_ENTITY_ID]
        if not recorder.allowed(entity_id):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_on_allowlist"
            )
        return build_report(
            entity_id=entity_id,
            records=recorder.buffer.records(now_ms=recorder.now_ms()),
            started_at=recorder.started_at,
        )

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_EXPLAIN,
        _explain,
        EXPLAIN_SCHEMA,
        SupportsResponse.ONLY,
    )
