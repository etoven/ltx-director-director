"""Live provider output bridge.

Each background generation worker owns a context-local callback. The provider
never reads or writes Qt widgets; UI receives events on Qt's main thread.
Only provider-published reasoning summaries are surfaced, not internal thought.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

STREAM_SINK: ContextVar[Callable[[str, str], None] | None] = ContextVar("stream_sink", default=None)


def send_stream(kind: str, content: str) -> None:
    callback = STREAM_SINK.get()
    if callback is not None and content:
        callback(kind, content)


def streaming_enabled() -> bool:
    return STREAM_SINK.get() is not None
