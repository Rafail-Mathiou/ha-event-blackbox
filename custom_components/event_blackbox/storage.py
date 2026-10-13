"""Bounded in-memory buffer for observed events.

Pure module without Home Assistant runtime and without any persistence. The
buffer is limited three ways: number of records, estimated bytes and age.
When a limit is reached, the oldest records are dropped and counted; nothing
ever blocks or grows without bound.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .normalizer import EventRecord, record_size


@dataclass(frozen=True, slots=True)
class BufferStats:
    """Counters of a buffer. Aggregates only, safe for diagnostics."""

    events: int
    bytes: int
    received: int
    rejected: int
    dropped_count: int
    dropped_size: int
    dropped_age: int


class EventBuffer:
    """Ring buffer limited by count, size and age."""

    def __init__(self, *, max_events: int, max_bytes: int, max_age_ms: int) -> None:
        """Create an empty buffer. All limits must be positive."""
        if min(max_events, max_bytes, max_age_ms) <= 0:
            msg = "buffer limits must be positive"
            raise ValueError(msg)
        self._max_events = max_events
        self._max_bytes = max_bytes
        self._max_age_ms = max_age_ms
        self._records: deque[tuple[EventRecord, int]] = deque()
        self._bytes = 0
        self._received = 0
        self._rejected = 0
        self._dropped_count = 0
        self._dropped_size = 0
        self._dropped_age = 0

    @property
    def max_events(self) -> int:
        """Limit on the number of records."""
        return self._max_events

    @property
    def max_bytes(self) -> int:
        """Limit on the estimated size in bytes."""
        return self._max_bytes

    @property
    def max_age_ms(self) -> int:
        """Limit on the age of records in milliseconds."""
        return self._max_age_ms

    def append(self, record: EventRecord, *, now_ms: int) -> bool:
        """Store a record, dropping the oldest ones as needed.

        Returns False when the record alone exceeds the byte limit.
        """
        self._received += 1
        size = record_size(record)
        if size > self._max_bytes:
            self._rejected += 1
            return False
        self._expire(now_ms)
        self._records.append((record, size))
        self._bytes += size
        while len(self._records) > self._max_events:
            self._pop_oldest()
            self._dropped_count += 1
        while self._bytes > self._max_bytes:
            self._pop_oldest()
            self._dropped_size += 1
        return True

    def records(self, *, now_ms: int | None = None) -> tuple[EventRecord, ...]:
        """Return a snapshot of the stored records, oldest first."""
        if now_ms is not None:
            self._expire(now_ms)
        return tuple(record for record, _size in self._records)

    def stats(self) -> BufferStats:
        """Return the current counters."""
        return BufferStats(
            events=len(self._records),
            bytes=self._bytes,
            received=self._received,
            rejected=self._rejected,
            dropped_count=self._dropped_count,
            dropped_size=self._dropped_size,
            dropped_age=self._dropped_age,
        )

    def clear(self) -> None:
        """Drop every record. Counters are kept until the buffer is discarded."""
        self._records.clear()
        self._bytes = 0

    def _pop_oldest(self) -> None:
        _record, size = self._records.popleft()
        self._bytes -= size

    def _expire(self, now_ms: int) -> None:
        cutoff = now_ms - self._max_age_ms
        while self._records and self._records[0][0].relative_ms < cutoff:
            self._pop_oldest()
            self._dropped_age += 1
