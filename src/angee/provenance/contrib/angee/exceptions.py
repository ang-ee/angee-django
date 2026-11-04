"""Angee wire errors for provenance operations."""

import sys
from pathlib import Path

from rebac.errors import MissingActorError

from angee.provenance.context import ChangesetRequired as ContextRequired
from angee.provenance.engine.exceptions import CreationKeyConflict as CreationKeyConflict

MANAGEMENT_ENTRYPOINT = "angee.server.management.execute"


class AngeeChangesetRequired(ContextRequired, MissingActorError):
    """Keep REBAC catchability and the native management-entrypoint remedy."""

    def __init__(self, message: str) -> None:
        if sys.argv and Path(sys.argv[0]).name == "manage.py":
            message += (
                f" This management command runs without a boundary: manage.py must call {MANAGEMENT_ENTRYPOINT}();"
                " update it to the native project launcher."
            )
        super().__init__(message)


ChangesetRequired = AngeeChangesetRequired
