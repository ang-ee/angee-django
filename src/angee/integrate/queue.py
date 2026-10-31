"""Historical queue import; Bridge owns admission and dispatch."""

from angee.integrate.models.bridges import Bridge

queue_bridge_sync = Bridge.request_sync

__all__ = ["queue_bridge_sync"]
