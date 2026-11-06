"""Host-owned admission and identity at the package boundary."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import (
    ImproperlyConfigured,
    ObjectDoesNotExist,
    PermissionDenied,
    ValidationError,
)
from django.db import models
from django.utils.module_loading import import_string


@runtime_checkable
class PermissionActor(Protocol):
    @property
    def is_authenticated(self) -> bool: ...

    def has_perm(self, perm: str, obj: object | None = None) -> bool: ...


class Policy:
    """Default Django model-permission policy; subclass for object permissions.

    Capture never calls this policy. Operations which expose or change retained
    information call it explicitly; no permission follows from attribution.
    The default uses global Django model permissions because Django's built-in
    ModelBackend does not implement object permission storage.
    """

    def authorize(self, actor: object, action: str, instance: models.Model) -> None:
        permission = f"{instance._meta.app_label}.{action}_{instance._meta.model_name}"
        if not isinstance(actor, PermissionActor) or not actor.is_authenticated or not actor.has_perm(permission):
            raise PermissionDenied(f"{permission} is required.")

    def resolve_source(self, source: object, *, using: str) -> models.Model:
        """Resolve one persisted native principal on the statement database.

        Hosts may decode their own explicit principal identity here. Resolution
        proves persistence, never authority; authorize remains a separate gate.
        """

        if not isinstance(source, models.Model) or source.pk is None or source._state.adding:
            raise ValidationError("An asserting source must be a persisted Django principal.")
        if source._state.db != using:
            raise ValidationError("The asserting source must exist on the subject database.")
        try:
            return type(source)._base_manager.using(using).get(pk=source.pk)
        except ObjectDoesNotExist as error:
            raise ValidationError("The asserting source must exist on the subject database.") from error

    def source_kind(self, source: models.Model) -> str:
        """Classify a persisted source; a label alone cannot assert a statement."""

        if source.pk is None or source._state.adding:
            raise ValidationError("A statement source must be a persisted Django model.")
        # This is an explicit public model contract, not shape-based discovery of
        # an unrelated model's implementation.
        from .statements import SourceKind, StatementSourceMixin

        if not isinstance(source, StatementSourceMixin):
            if isinstance(source, get_user_model()):
                return "person"
            raise ValidationError("A statement source must implement StatementSourceMixin.")
        kind = source.source_kind
        if kind is None:
            raise ValidationError("The principal declares an unknown source kind.")
        try:
            return str(SourceKind(kind))
        except (TypeError, ValueError) as error:
            raise ValidationError("The principal declares an unknown source kind.") from error

    def history_user(self, actor: object) -> models.Model | None:
        """Only persisted native auth users supply simple-history's user FK."""

        if isinstance(actor, get_user_model()) and actor.pk is not None and not actor._state.adding:
            return actor
        return None


def get_policy() -> Policy:
    """Resolve the configured policy lazily so Django override_settings works."""

    configured = getattr(settings, "PROVENANCE_POLICY", Policy)
    candidate = import_string(configured) if isinstance(configured, str) else configured
    result = candidate() if isinstance(candidate, type) else candidate
    if not isinstance(result, Policy):
        raise ImproperlyConfigured("PROVENANCE_POLICY must be a Policy subclass or instance.")
    return result
