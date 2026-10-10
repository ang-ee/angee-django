"""Values crossing connection discovery's network/database boundary.

Connection discovery locates an external resource. It is distinct from a REBAC
binding, which grants a relationship. Durable ``binding_*`` names are retained
as the integration's public handshake contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DerivedCredential:
    """Provider-less material to persist through the credential owner after fencing.

    ``name`` is the credential's per-user identity. Secret values stay in this
    in-process result and are never serialized to task payloads or telemetry.
    """

    kind: str
    name: str
    material: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ConnectionDiscovery:
    """Remote facts interpreted by the adapter's database-only apply hook."""

    data: Mapping[str, Any] = field(default_factory=dict, repr=False)
    credential: DerivedCredential | None = field(default=None, repr=False)
