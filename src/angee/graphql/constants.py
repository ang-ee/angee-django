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

CHANGE_REPLAY_SECONDS = 60
"""Age of changes available to a newly registered subscriber."""

CHANGE_REPLAY_MAXLEN = 1024
"""Maximum retained changes per model group before a full-read fallback."""
