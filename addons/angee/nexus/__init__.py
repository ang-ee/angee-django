"""Nexus — party-graph relationship analytics over messaging.

The canonical pair edges in :class:`~angee.nexus.models.Tie` are computed,
never authoritative: deliberate addressed messages, replies, and mentions can
rebuild every count, timestamp, gravity score, and fading signal. The separate
:class:`~angee.nexus.models.Cadence` micro-model is the sole human fact, recording
one user's stay-in-touch intent for one party. The cross-channel party timeline
remains a read delegated to messaging.
"""
