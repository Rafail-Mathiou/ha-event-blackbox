"""The in-memory buffer stays within its count, size and age limits."""

from __future__ import annotations

import time

import pytest

from custom_components.event_blackbox.normalizer import (
    EventKind,
    EventRecord,
    record_size,
)
from custom_components.event_blackbox.storage import EventBuffer

CTX = "01SYNTHETICCONTEXT00000001"


def _record(seq: int, ms: int = 0, entity: str = "light.demo_hall") -> EventRecord:
    return EventRecord(
        event_id=seq,
        relative_ms=ms,
        kind=EventKind.STATE_CHANGE,
        entity_ref=entity,
        transition="off_to_on",
        context_ref=CTX,
        parent_context_ref=None,
    )


def _buffer(
    max_events: int = 100, max_bytes: int = 1_000_000, max_age_ms: int = 60_000
) -> EventBuffer:
    return EventBuffer(
        max_events=max_events, max_bytes=max_bytes, max_age_ms=max_age_ms
    )


def test_buffer_starts_empty() -> None:
    """A new buffer holds nothing and has zero counters."""
    buffer = _buffer()
    assert buffer.records() == ()
    stats = buffer.stats()
    assert stats.events == 0
    assert stats.bytes == 0
    assert stats.received == 0


def test_count_limit_drops_oldest_and_counts() -> None:
    """When full, the oldest records go first and are counted."""
    buffer = _buffer(max_events=3)
    for seq in range(1, 6):
        assert buffer.append(_record(seq), now_ms=0)
    assert [r.event_id for r in buffer.records()] == [3, 4, 5]
    stats = buffer.stats()
    assert (stats.events, stats.received, stats.dropped_count) == (3, 5, 2)


def test_byte_limit_drops_oldest_and_counts() -> None:
    """The size estimate never exceeds the byte limit."""
    size = record_size(_record(1))
    buffer = _buffer(max_bytes=size * 2)
    for seq in range(1, 5):
        buffer.append(_record(seq), now_ms=0)
    stats = buffer.stats()
    assert stats.bytes <= size * 2
    assert stats.events == 2
    assert stats.dropped_size == 2


def test_record_larger_than_limit_is_rejected() -> None:
    """A single record that can never fit is rejected, not stored."""
    buffer = _buffer(max_bytes=10)
    assert buffer.append(_record(1), now_ms=0) is False
    stats = buffer.stats()
    assert (stats.events, stats.rejected) == (0, 1)


def test_age_limit_expires_old_records_on_append_and_read() -> None:
    """Records older than the age limit disappear, counted as expired."""
    buffer = _buffer(max_age_ms=1_000)
    buffer.append(_record(1, ms=0), now_ms=0)
    buffer.append(_record(2, ms=500), now_ms=500)
    buffer.append(_record(3, ms=1_400), now_ms=1_400)
    assert [r.event_id for r in buffer.records(now_ms=1_400)] == [2, 3]
    assert [r.event_id for r in buffer.records(now_ms=2_600)] == []
    assert buffer.stats().dropped_age == 3
    assert buffer.stats().bytes == 0


def test_clear_drops_everything() -> None:
    """Clearing removes all records and resets the size."""
    buffer = _buffer()
    for seq in range(1, 4):
        buffer.append(_record(seq), now_ms=0)
    buffer.clear()
    assert buffer.records() == ()
    assert buffer.stats().bytes == 0


@pytest.mark.parametrize(
    ("max_events", "max_bytes", "max_age_ms"),
    [(0, 1, 1), (1, 0, 1), (1, 1, 0), (-1, 1, 1)],
)
def test_invalid_limits_are_refused(
    max_events: int, max_bytes: int, max_age_ms: int
) -> None:
    """A buffer without a positive limit is a programming error."""
    with pytest.raises(ValueError, match="positive"):
        EventBuffer(max_events=max_events, max_bytes=max_bytes, max_age_ms=max_age_ms)


def test_hundred_thousand_events_stay_within_limits() -> None:
    """Load test: 100,000 synthetic events, limits hold and appends stay cheap."""
    max_bytes = 2048 * 1024
    buffer = _buffer(max_events=2000, max_bytes=max_bytes, max_age_ms=30 * 60_000)
    started = time.perf_counter()
    for seq in range(1, 100_001):
        buffer.append(_record(seq, ms=seq, entity=f"light.demo_{seq % 50}"), now_ms=seq)
        if seq % 10_000 == 0:
            stats = buffer.stats()
            assert stats.events <= 2000
            assert stats.bytes <= max_bytes
    elapsed = time.perf_counter() - started
    stats = buffer.stats()
    assert stats.received == 100_000
    assert stats.events == 2000
    assert stats.dropped_count == 98_000
    # Generous budget: about 10 microseconds per event on a laptop.
    assert elapsed < 5.0
