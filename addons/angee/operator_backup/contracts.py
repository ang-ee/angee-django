"""Versioned capture intent; operator owns discovery and archive facts."""

from enum import StrEnum
from typing import Literal

from django.core.exceptions import ValidationError
from django.db import models
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError


class BackupTarget(models.TextChoices):
    """The operator target this addon currently supports."""

    LOCAL = "local", "Local stack"


class BackupResourceGroup(StrEnum):
    """Native operator discovery groups matching the selection document's keys."""

    DATABASES = "databases"
    SERVICE_STATE = "service_state"
    WORKSPACES = "workspaces"
    SOURCES = "sources"
    STORAGE = "storage"


class BackupResourceStatus(StrEnum):
    """The operator's closed inclusion and capability reports for one resource."""

    INCLUDED = "included"
    EXCLUDED = "excluded"
    UNSUPPORTED = "unsupported"


class GroupSelection(BaseModel):
    """Select named discovered resources, then remove explicit exclusions."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    mode: Literal["all", "selected", "none"] = "all"
    names: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_names(self) -> GroupSelection:
        """Keep IDs opaque, nonempty and unique; refuse contradictory intent."""
        for names in (self.names, self.exclude):
            if len(names) != len(set(names)) or any(not name.strip() or "\x00" in name for name in names):
                raise ValueError("Resource names must be nonempty and distinct.")
        if self.names and self.mode != "selected":
            raise ValueError("Explicit names require selected mode.")
        if set(self.names).intersection(self.exclude):
            raise ValueError("A resource cannot be both selected and excluded.")
        return self


class SourceSelection(GroupSelection):
    """Portable source evidence, never silently discard unpublished state."""

    policy: Literal["bundle", "fetch_published"] = "bundle"


class BackupSelection(BaseModel):
    """Selection document shared with the operator's version-one capture API."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: Literal[1] = 1
    databases: GroupSelection = Field(default_factory=GroupSelection)
    service_state: GroupSelection = Field(default_factory=GroupSelection)
    workspaces: GroupSelection = Field(default_factory=GroupSelection)
    sources: SourceSelection = Field(default_factory=SourceSelection)
    storage: GroupSelection = Field(default_factory=GroupSelection)


def default_selection() -> dict:
    """Return independent defaults for a new plan."""
    return BackupSelection().model_dump(mode="json")


def validate_selection(value: object) -> None:
    """Expose the typed contract as a native Django field validator."""
    try:
        BackupSelection.model_validate(value)
    except PydanticValidationError as error:
        raise ValidationError("Invalid backup selection: %(detail)s", params={"detail": str(error)}) from error


class CaptureInput(BaseModel):
    """Immutable run snapshot; the local destination is operator configured."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    selection: BackupSelection
    target_stack: BackupTarget = BackupTarget.LOCAL
    destination: Literal["local"] = "local"
