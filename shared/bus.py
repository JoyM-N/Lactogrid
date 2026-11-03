"""Message bus. Tests use the in-process bus. MQTT is a second implementation."""

from __future__ import annotations

import copy
import threading
from collections import defaultdict
from typing import Callable, Protocol


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT-style match. ``+`` is one level, ``#`` is the rest of the topic."""
    pattern_parts = pattern.split("/")
    topic_parts = topic.split("/")
    for index, part in enumerate(pattern_parts):
        if part == "#":
            return True
        if index >= len(topic_parts):
            return False
        if part == "+":
            continue
        if part != topic_parts[index]:
            return False
    return len(pattern_parts) == len(topic_parts)


Handler = Callable[[str, dict], None]


class MessageBus(Protocol):
    def publish(self, topic: str, body: dict) -> None: ...

    def subscribe(self, topic: str, handler: Handler) -> None: ...


class InProcessBus:
    """Thread-safe bus. Handlers receive a deep copy of the payload."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.Lock()

    def publish(self, topic: str, body: dict) -> None:
        with self._lock:
            chosen = [
                handler
                for pattern, handlers in self._handlers.items()
                if topic_matches(pattern, topic)
                for handler in handlers
            ]
        payload = copy.deepcopy(body)
        for handler in chosen:
            handler(topic, copy.deepcopy(payload))

    def subscribe(self, topic: str, handler: Handler) -> None:
        with self._lock:
            self._handlers[topic].append(handler)
