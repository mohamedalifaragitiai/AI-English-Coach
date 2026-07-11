"""Core infrastructure: resource guard, event bus, metrics, logging."""

from backend.core.event_bus import EventBus
from backend.core.resource_guard import ResourceGuard

__all__ = ["ResourceGuard", "EventBus"]
