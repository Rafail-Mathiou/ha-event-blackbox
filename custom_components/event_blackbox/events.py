"""Subscribe to the allowlisted events and feed the in-memory buffer.

The only module that listens to the Home Assistant event bus. It subscribes
only when collection is active (opt-in and a non-empty allowlist), uses
documented helpers only, does no I/O in its callbacks and never
logs event content. Every listener is released through the config entry on
unload, and the buffer is cleared with it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
import itertools
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
import homeassistant.util.dt as dt_util

from .const import (
    EVENT_TYPE_AUTOMATION_TRIGGERED,
    EVENT_TYPE_CALL_SERVICE,
    EVENT_TYPE_SCRIPT_STARTED,
)
from .normalizer import (
    EventRecord,
    normalize_automation_triggered,
    normalize_script_started,
    normalize_service_call,
    normalize_state_change,
    restore_record,
)
from .options import SENSITIVE_DOMAINS, BlackboxOptions
from .storage import EventBuffer

# Malformed payloads raise one of these while being read; they are counted,
# never logged with content, and never interrupt Home Assistant.
_PAYLOAD_ERRORS = (AttributeError, KeyError, TypeError, ValueError)


class Recorder:
    """Turns allowlisted events into records in a bounded buffer."""

    def __init__(
        self,
        options: BlackboxOptions,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the recorder and its empty buffer.

        With history on, the age limit is the number of days.
        """
        max_age_ms = (
            options.history_days * 86_400_000
            if options.history
            else options.max_age_minutes * 60_000
        )
        self.buffer = EventBuffer(
            max_events=options.max_events,
            max_bytes=options.max_kib * 1024,
            max_age_ms=max_age_ms,
        )
        self.entities = frozenset(options.entities)
        self.domains = frozenset(options.domains)
        self.ignored = 0
        self.errors = 0
        self._monotonic = monotonic
        self._start = monotonic()
        # Wall clock time of the start, only to show record times in reports.
        self.started_at = dt_util.utcnow()
        self._seq = itertools.count(1)
        # Called after every stored record, for example to schedule a save.
        self.on_record: Callable[[], None] | None = None

    def stored(self) -> list[dict[str, Any]]:
        """Return the buffer as plain data with wall clock times, for saving."""
        return [
            {
                **record.as_dict(),
                "wall": (
                    self.started_at + timedelta(milliseconds=record.relative_ms)
                ).isoformat(),
            }
            for record in self.buffer.records(now_ms=self.now_ms())
        ]

    def restore(self, entries: list[Any]) -> int:
        """Load saved records before recording starts. Returns how many were kept.

        Saved data is untrusted: every entry is validated, times are converted
        to this recording's clock, and records keep their order with new
        sequence numbers before the live ones.
        """
        restored: list[EventRecord] = []
        for entry in entries:
            wall = (
                dt_util.parse_datetime(entry.get("wall", ""))
                if isinstance(entry, dict)
                else None
            )
            if wall is None or wall.tzinfo is None:
                continue
            relative_ms = int((wall - self.started_at).total_seconds() * 1000)
            record = restore_record(
                entry, event_id=len(restored) + 1, relative_ms=relative_ms
            )
            if record is not None and -self.buffer.max_age_ms <= relative_ms <= 0:
                restored.append(record)
        restored.sort(key=lambda record: record.relative_ms)
        for index, record in enumerate(restored, start=1):
            self.buffer.append(replace(record, event_id=index), now_ms=0)
        self._seq = itertools.count(len(restored) + 1)
        return len(self.buffer.records())

    def allowed(self, entity_id: str) -> bool:
        """Return whether an entity is on the allowlist (directly or by domain)."""
        return entity_id in self.entities or entity_id.split(".", 1)[0] in self.domains

    def now_ms(self) -> int:
        """Milliseconds since the recorder started (monotonic clock)."""
        return int((self._monotonic() - self._start) * 1000)

    @callback
    def domain_filter(self, data: EventStateChangedData) -> bool:
        """Bus filter: allowlisted domains, without entities tracked directly."""
        entity_id = data["entity_id"]
        return (
            entity_id not in self.entities
            and entity_id.split(".", 1)[0] in self.domains
        )

    @callback
    def handle_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Record a state change of an allowlisted entity."""
        try:
            data = event.data
            old_state = data["old_state"]
            new_state = data["new_state"]
            entity_id = data["entity_id"]
            record = normalize_state_change(
                seq=next(self._seq),
                relative_ms=self.now_ms(),
                entity_id=entity_id,
                old_state=None if old_state is None else old_state.state,
                new_state=None if new_state is None else new_state.state,
                context_id=event.context.id,
                parent_id=event.context.parent_id,
                minimal=entity_id.split(".", 1)[0] in SENSITIVE_DOMAINS,
            )
        except _PAYLOAD_ERRORS:
            self.errors += 1
            return
        self._store(record)

    @callback
    def handle_automation_triggered(self, event: Event[dict[str, Any]]) -> None:
        """Record an automation run (needed to link triggers and actions)."""
        try:
            record = normalize_automation_triggered(
                seq=next(self._seq),
                relative_ms=self.now_ms(),
                entity_id=event.data["entity_id"],
                context_id=event.context.id,
                parent_id=event.context.parent_id,
            )
        except _PAYLOAD_ERRORS:
            self.errors += 1
            return
        self._store(record)

    @callback
    def handle_script_started(self, event: Event[dict[str, Any]]) -> None:
        """Record a script run."""
        try:
            record = normalize_script_started(
                seq=next(self._seq),
                relative_ms=self.now_ms(),
                entity_id=event.data["entity_id"],
                context_id=event.context.id,
                parent_id=event.context.parent_id,
            )
        except _PAYLOAD_ERRORS:
            self.errors += 1
            return
        self._store(record)

    @callback
    def handle_service_call(self, event: Event[dict[str, Any]]) -> None:
        """Record a service call that targets an allowlisted entity."""
        try:
            data = event.data
            service_data = data.get("service_data") or {}
            record = normalize_service_call(
                seq=next(self._seq),
                relative_ms=self.now_ms(),
                domain=data["domain"],
                service=data["service"],
                service_data=service_data,
                context_id=event.context.id,
                parent_id=event.context.parent_id,
                allowed=self.allowed,
            )
        except _PAYLOAD_ERRORS:
            self.errors += 1
            return
        self._store(record)

    def _store(self, record: EventRecord | None) -> None:
        if record is None:
            self.ignored += 1
            return
        self.buffer.append(record, now_ms=self.now_ms())
        if self.on_record is not None:
            self.on_record()


def async_subscribe(
    hass: HomeAssistant, entry: ConfigEntry[Any], recorder: Recorder
) -> None:
    """Subscribe the recorder to the allowlisted events, released on unload."""
    if recorder.entities:
        entry.async_on_unload(
            async_track_state_change_event(
                hass, sorted(recorder.entities), recorder.handle_state_changed
            )
        )
    if recorder.domains:
        entry.async_on_unload(
            hass.bus.async_listen(
                EVENT_STATE_CHANGED,
                recorder.handle_state_changed,
                event_filter=recorder.domain_filter,
            )
        )
    for event_type, handler in (
        (EVENT_TYPE_AUTOMATION_TRIGGERED, recorder.handle_automation_triggered),
        (EVENT_TYPE_SCRIPT_STARTED, recorder.handle_script_started),
        (EVENT_TYPE_CALL_SERVICE, recorder.handle_service_call),
    ):
        entry.async_on_unload(hass.bus.async_listen(event_type, handler))
    entry.async_on_unload(recorder.buffer.clear)
