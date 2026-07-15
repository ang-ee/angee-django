"""A locally scoped, create-gated document for create-gate regression tests.

``ScopedDoc`` composes this test app's local ``ScopeScopedMixin``. The blank-on-
input ``scope`` FK defaults from the actor's sole direct scope membership and
the adjacent ``permissions.zed`` gates ``create`` on ``scope->member``. That arm
fail-closes unless the auto-CRUD create preflight evaluates against the defaulted
scope, so these models keep coverage on Angee's generic create-default machinery
without depending on any framework-owned business scope.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from django.core.exceptions import ValidationError
from django.db import models
from rebac import app_settings, current_actor, is_anonymous_actor, to_subject_ref
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type

from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet

SCOPE_MEMBER_RELATION = "direct_member"
"""Direct membership relation on the local ``scopedemo/scope`` test resource."""


class ScopeQuerySet(AngeeQuerySet[Any]):
    """Queryset vocabulary for local create-gate scopes."""


class ScopeManager(AngeeManager.from_queryset(ScopeQuerySet)):  # type: ignore[misc]
    """Manager for the test-local scope resource."""

    def direct_memberships_of(self, actor: Any) -> ScopeQuerySet:
        """Return scopes where ``actor`` has the direct test membership tuple."""

        subject = to_subject_ref(actor)
        id_attr = str(getattr(self.model._meta, "rebac_id_attr", None) or app_settings.REBAC_RESOURCE_ID_ATTR)
        scope_ids = list(
            active_relationship_model()
            .objects.filter(
                resource_type=model_resource_type(self.model),
                relation=SCOPE_MEMBER_RELATION,
                subject_type=subject.subject_type,
                subject_id=subject.subject_id,
                optional_subject_relation=subject.optional_relation,
            )
            .values_list("resource_id", flat=True)
        )
        scope_free = cast(
            ScopeQuerySet,
            self.get_queryset().system_context(reason="direct scopedemo membership is itself the authorization"),
        )
        return scope_free.filter(**{f"{id_attr}__in": scope_ids})


class Scope(AngeeDataModel):
    """A test-local REBAC scope with parent reach and direct membership."""

    sqid_prefix = "scp_"

    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        "scopedemo.Scope",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )

    objects = ScopeManager()

    class Meta(AngeeDataModel.Meta):
        """Concrete test scope used by generic create-gate and zed tests."""

        abstract = False
        app_label = "scopedemo"
        db_table = "test_scopedemo_scope"
        rebac_resource_type = "scopedemo/scope"
        rebac_id_attr = "sqid"


class ScopeScopedMixin(models.Model):
    """Scope a test model's rows to one local ``Scope``."""

    scope = models.ForeignKey(
        "scopedemo.Scope",
        on_delete=models.PROTECT,
        related_name="+",
        blank=True,
    )

    class Meta:
        """Django model options for scope-only abstract inheritance."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Default an unset ``scope`` from the acting user's sole membership."""

        if self.scope_id is None:
            self.scope = self._default_scope_from_membership()
        super().save(*args, **kwargs)

    def apply_create_defaults(self) -> Mapping[str, Sequence[Any]]:
        """Default blank ``scope`` before the create gate evaluates the row."""

        contributions = dict(super().apply_create_defaults())
        if self.scope_id is None:
            self.scope = self._default_scope_from_membership()
            contributions["scope"] = (self.scope,)
        return contributions

    def _default_scope_from_membership(self) -> Scope:
        """Return the acting user's sole direct scope, or raise naming ``scope``."""

        actor = current_actor()
        if actor is None or is_anonymous_actor(actor):
            raise ValidationError({"scope": "Select a scope: it cannot be defaulted for an unauthenticated actor."})
        scope_model = type(self)._meta.get_field("scope").related_model
        memberships = list(scope_model._default_manager.direct_memberships_of(actor)[:2])
        if len(memberships) == 1:
            return memberships[0]
        if not memberships:
            raise ValidationError(
                {"scope": "Select a scope: you are not a direct member of any scope to default from."}
            )
        raise ValidationError(
            {"scope": "Select a scope: you are a direct member of several, so it cannot be defaulted."}
        )


class ScopedDoc(ScopeScopedMixin, AngeeDataModel):
    """A locally scoped document whose ``create`` rides ``scope->member``."""

    sqid_prefix = "scd_"

    title = models.CharField(max_length=200, blank=True, default="")

    class Meta(AngeeDataModel.Meta):
        """Concrete locally scoped, create-gated document for the gate tests."""

        abstract = False
        app_label = "scopedemo"
        db_table = "test_scopedemo_doc"
        rebac_resource_type = "scopedemo/doc"
        rebac_id_attr = "sqid"
