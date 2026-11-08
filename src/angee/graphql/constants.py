"""Lightweight GraphQL constants safe for settings composition."""

from __future__ import annotations

PUBLIC_ID_FIELD_NAME = "sqid"
"""Model field name Angee exposes as the public GraphQL pk."""

CHANGE_GROUP_EXPIRY_SECONDS = 900
"""Channel-layer group lease (the layer-wide ``group_expiry``).

A subscriber's membership outlives its connection only when its process dies
without discarding it (a restart or autoreload); the lease bounds that leak.
Every group member on this layer must renew within it; change subscriptions
are the only group users today.
"""

CHANGE_GROUP_RENEW_SECONDS = 300
"""How often a live subscriber renews its change-group lease (well inside expiry)."""

CHANNEL_LAYER_SOCKET_TIMEOUT_SECONDS = 15.0
"""Redis socket read timeout for the channel layer's connections.

A consumer waits for messages with a blocking ``BZPOPMIN`` of up to
``RedisChannelLayer.brpop_timeout`` (5 s). redis-py 8 defaults every socket read
to 5 s, which cuts that wait off at its own boundary and crashes the websocket
consumer; the layer's reads must outlast the blocking pop.
"""
