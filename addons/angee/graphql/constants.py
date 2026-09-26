"""Lightweight GraphQL constants safe for settings composition."""

from __future__ import annotations

PUBLIC_ID_FIELD_NAME = "sqid"
"""Model field name Angee exposes as the public GraphQL pk."""

CHANGE_GROUP_EXPIRY_SECONDS = 900
"""Channel-layer lease on a change-group membership.

A subscriber's membership outlives its connection only when its process dies
without discarding it (a restart or autoreload); the lease bounds that leak.
"""

CHANGE_GROUP_RENEW_SECONDS = 300
"""How often a live subscriber renews its change-group lease (well inside expiry)."""
