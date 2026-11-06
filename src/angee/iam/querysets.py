"""Native user selection and account policy scopes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self, cast

from django.db import models
from django.db.models import Exists, F, OuterRef, Q, TextField
from django.db.models.functions import Cast, Coalesce
from rebac import to_subject_ref
from rebac.managers import RebacQuerySet, TrackedQuerySet
from rebac.resources import model_resource_type
from rebac.roles import ROLE_RELATION

from angee.base.querysets import AngeeQuerySet
from angee.iam.choices import UserKind
from angee.iam.permissions import actor_user_id, instance_actor
from angee.iam.roles import protected_account_holders

if TYPE_CHECKING:
    from angee.iam.models import User


class ProtectedQuerySet[ModelT: models.Model](AngeeQuerySet[ModelT], RebacQuerySet[ModelT]):
    """Native actor-scoped collection and readable correlated projections."""

    def attributed_user_id(self) -> Any:
        """Keep the requesting user when an owning bulk write bypasses read scoping."""

        return actor_user_id(instance_actor(self))

    def readable_scalar_subquery(
        self,
        field: str,
        *,
        actor: Any = None,
        default: Any = None,
        output_field: models.Field | None = None,
    ) -> models.Expression | models.Subquery:
        """Project one correlated readable value, including under an elevated parent.

        Add correlation predicates before calling. No actor yields no value;
        callers own domain fallbacks and may explicitly coalesce an absent value.
        """

        actor = actor or instance_actor(self)
        readable = self.with_actor(actor).scoped() if actor is not None else self
        # The selected field owns the scalar type; only the outer fallback needs
        # a common output type (overriding Subquery changes empty-set compilation).
        scalar = models.Subquery(readable.values(field)[:1])
        if actor is None:
            return models.Value(default, output_field=output_field or scalar.output_field)
        if default is None:
            return scalar
        return Coalesce(
            scalar,
            models.Value(default),
            output_field=output_field or scalar.output_field,
        )

    def readable_count_subquery(self, *, actor: Any = None) -> models.Expression | models.Subquery:
        """Count a correlated actor-scoped row set, returning zero when empty."""

        rows = self.order_by().annotate(_count_group=models.Value(1)).values("_count_group")
        return rows.annotate(_count=models.Count("pk", distinct=True)).readable_scalar_subquery(
            "_count",
            actor=actor,
            default=0,
            output_field=models.IntegerField(),
        )

    def _keyset_cursor_scope(self, scope: tuple[Any, ...]) -> tuple[Any, ...]:
        return (*scope, str(instance_actor(self)))


class ProtectedUnscopedQuerySet[ModelT: models.Model](AngeeQuerySet[ModelT], TrackedQuerySet[ModelT]):
    """Unfiltered native Django reads with REBAC-tracked protected writes."""


class UserQuerySet[UserT: User](ProtectedQuerySet[UserT]):
    """Queryset vocabulary for user-row surfaces."""

    def people(self) -> Self:
        """Return login-capable human users, excluding service-account rows."""

        return self.filter(kind=UserKind.PERSON)

    def active_people(self) -> Self:
        """Return active human user rows.

        Activity is a restricted field (``read__is_active``), yet pickers and
        preview offer only active people: a deliberate disclosure IAM's Zed
        declares (``active_account``). An inactive account is absent; its state
        is never shown. See :meth:`inactive_condition`.
        """

        actor, unscoped = self.effective_actor()
        return self.people().exclude(self.inactive_condition(None if unscoped else actor))

    def inactive_condition(self, actor: Any = None) -> Q:
        """Match inactive accounts as IAM's Zed discloses them to ``actor`` (``active_account``).

        Without an actor (a system caller) the column decides directly.
        """

        if actor is None:
            return Q(is_active=False)
        return ~Q(pk__in=self.model._default_manager.with_actor(actor).with_action("active_account").values("pk"))

    def search_users(self, search: str) -> Self:
        """Filter users by the fields exposed by IAM identity pickers.

        The restricted sign-in name and email match only where the actor reads
        them, so a search never probes a field the actor cannot see.
        """

        term = search.strip()
        if not term:
            return cast(Self, self)
        return cast(
            Self,
            self.alias(
                _iam_search_username=self.readable("username"),
                _iam_search_email=self.readable("email"),
            ).filter(
                Q(_iam_search_username__icontains=term)
                | Q(first_name__icontains=term)
                | Q(last_name__icontains=term)
                | Q(_iam_search_email__icontains=term)
            ),
        )

    def ordered_users(self) -> Self:
        """Order by sign-in name where the actor reads it, then by name and primary key."""

        return cast(
            Self,
            self.alias(_iam_order_username=self.readable("username")).order_by(
                F("_iam_order_username").asc(nulls_last=True),
                "first_name",
                "last_name",
                "pk",
            ),
        )

    def picker(self, search: str) -> Self:
        """Apply the common active-person search and ordering pipeline."""

        return self.active_people().search_users(search).ordered_users()

    def protection_condition(self, actor: Any = None) -> Q:
        """Match accounts whose authority others must not act on or assume.

        Staff, superusers and effective members of IAM's ``iam/protected`` Zed
        set — platform admins plus every elevated role a consumer unions into
        its ``member`` permission — are protected from every actor; an actor's
        own account is protected from that actor. Account verbs, generic
        updates by non-administrators and view-as share this one rule. Staff is
        a restricted field (``read__is_staff``): within the reach of preview,
        updates and the account verbs, IAM's Zed tells ``actor`` which accounts are
        not staff (``nonstaff_account``), and every other account counts as
        protected, so the rule fails closed. A system caller (no actor) reads the
        column directly.
        """

        if actor is None:
            condition = Q(is_staff=True)
        else:
            nonstaff = self.model._default_manager.with_actor(actor).with_action("nonstaff_account")
            condition = ~Q(pk__in=nonstaff.values("pk"))
        condition |= Q(is_superuser=True)
        holders = protected_account_holders()
        if holders is None:
            condition |= Q(pk__isnull=False)
        elif holders:
            condition |= Q(pk__in=sorted(holders))
        subject = to_subject_ref(actor) if actor is not None else None
        if (
            subject is not None
            and subject.subject_type == model_resource_type(self.model)
            and not subject.optional_relation
            and subject.subject_id not in {"", "*"}
        ):
            condition |= Q(pk=subject.subject_id)
        return condition

    def unprotected(self, actor: Any = None) -> Self:
        """Exclude the accounts :meth:`protection_condition` protects from ``actor``."""

        return cast(Self, self.exclude(self.protection_condition(actor)))

    def preview_candidates(self, actor: Any) -> Self:
        """Return the active, unprotected people ``actor`` may assume in a preview."""

        return self.active_people().unprotected(actor)

    def without_direct_roles(self, grant_rows: Any, role_resource_types: set[str]) -> Self:
        """Return users without direct role memberships in the installed schema."""

        user_grants = grant_rows.filter(
            resource_type__in=role_resource_types,
            relation=ROLE_RELATION,
            subject_type=model_resource_type(self.model),
            optional_subject_relation="",
        )
        pk = self.model._meta.pk
        subject_lookup = pk.name if pk is not None else "pk"
        users = self.annotate(_iam_subject_id=Cast(subject_lookup, output_field=TextField()))
        assigned = user_grants.filter(subject_id=OuterRef("_iam_subject_id"))
        return cast(Self, users.annotate(_iam_has_role=Exists(assigned)).filter(_iam_has_role=False))

    def directory(self, actor: Any) -> Self:
        """Return the accounts ``actor`` lists in the people directory, scoped by ``auth/user#list``.

        People managers list every account; anyone else lists nobody.
        """

        return cast(Self, self.with_actor(actor).with_action("list"))

    def colleagues(self, actor: Any) -> Self:
        """Return the accounts ``actor`` is offered in pickers, scoped by ``auth/user#colleague``.

        People managers are offered every account; anyone else their own and
        the arms identity owners contribute, such as the active people whose
        identity they read through records.
        """

        return cast(Self, self.with_actor(actor).with_action("colleague"))

    def view_as_queryset(self, actor: Any) -> Self:
        """The single admission scope used by the picker and header lookup."""

        subject = to_subject_ref(actor)
        return self.with_actor(subject).with_action("view_as").preview_candidates(subject)
