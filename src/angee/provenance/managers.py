"""Changeset storage scopes and atomic creation claims."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from angee.iam.managers import ProtectedManager
from angee.provenance.changeset_querysets import ChangesetQuerySet as ChangesetQuerySet
from angee.provenance.contrib.angee.exceptions import CreationKeyConflict
from angee.provenance.contrib.angee.serialization import creation_fingerprint
from angee.provenance.engine.managers import ChangesetManager as NativeChangesetManager

if TYPE_CHECKING:
    from angee.provenance.models import Changeset


_ChangesetManagerBase = ProtectedManager.from_queryset(ChangesetQuerySet)


class ChangesetManager[ChangesetT: Changeset](_ChangesetManagerBase[ChangesetT], NativeChangesetManager):
    """Native atomic claims with Angee's historical content and error contracts."""

    creation_conflict = CreationKeyConflict

    @staticmethod
    def creation_fingerprint(value: Any) -> str:
        return creation_fingerprint(value)

    @staticmethod
    def claim_field_name(name: str) -> str:
        return "idempotency_key" if name == "key" else name
