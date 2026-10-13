"""Constants for the Event Blackbox integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "event_blackbox"
NAME: Final = "Event Blackbox"


# Version of the internal data contract (docs/ARCHITECTURE.md).
DATA_SCHEMA_VERSION: Final = 1


# Documented Home Assistant event types (see docs/ARCHITECTURE.md).
EVENT_TYPE_AUTOMATION_TRIGGERED: Final = "automation_triggered"
EVENT_TYPE_CALL_SERVICE: Final = "call_service"
EVENT_TYPE_SCRIPT_STARTED: Final = "script_started"

# Whether events are recorded. Recording needs opt-in and an allowlist.
COLLECTION_STATUS_OFF: Final = "OFF"
COLLECTION_STATUS_RECORDING: Final = "RECORDING"
