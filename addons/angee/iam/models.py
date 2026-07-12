"""Source models for Angee identity.

Pure identity: the swappable ``User`` and its manager, plus the ``Company`` of
record — the operating entity acting *inside* the system, an access-control
scope with a REBAC hierarchy. The OAuth connection substrate
(``OAuthClient``/``ExternalAccount``/``Credential``) is owned by ``integrate``;
OIDC login fields are contributed onto that OAuth client by
``iam_integrate_oidc``.

Company scope convention
------------------------
Two "company" concepts must never be conflated. An *external* company (a
customer, vendor, any counterparty — a description of the outside world) is a
``parties.Organization``, never modelled here. The *company of record* — the
entity that acts inside the system — is this ``iam.Company``: an access-control
scope, so it belongs to ``iam`` (the lowest addon in the dependency order) and
carries no fiscal fields and no party FK. Its public face (a link to a
``parties.Party`` for name/addresses/logo) is a same-row ``extends`` merge owned
by ``angee.parties``; its fiscal face (currency, counterpart accounts, rounding
policy) is a same-row ``extends`` merge owned by a downstream accounting addon —
both downstream, so the dependency stays one-way.

Every model whose rows belong to a company of record composes
:class:`CompanyScopedMixin` (the ``company`` FK) and adds the matching arm to its
own ``permissions.zed`` definition, so isolation is enforced from day one::

    definition <app>/<model> {
        relation company: iam/company // rebac:field=company
        relation admin:   angee/role   // rebac:const=admin

        permission read   = company->member + admin->member
        permission write  = company->member + admin->member
        permission delete = company->member + admin->member
    }

Company-scoped role bindings are relations *on the company*, so a scoped resource
reads ``company-><role>`` — a role *of company A*, never a global role. The base
``iam/company`` definition names no such role: a consumer addon contributes each
one additively through the ``permissions.extends.zed`` seam
(``angee.compose.permissions``), keeping its domain vocabulary in the addon that
owns the concern. Subsidiaries inherit reach through ``parent``
(``permission member = direct_member + parent->member``): an ancestor-company
member reaches every descendant scope.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from rebac import (
    app_settings,
    current_actor,
    is_anonymous_actor,
    system_context,
    to_subject_ref,
)
from rebac.models import active_relationship_model
from rebac.permissions_mixin import RebacPermissionsMixin
from rebac.resources import model_resource_type
from rebac.roles import grant, revoke

from angee.base.fields import StateField
from angee.base.mixins import ArchiveMixin, ArchiveQuerySet, SqidMixin
from angee.base.models import AngeeDataModel, AngeeManager, AngeeModel, AngeeQuerySet


class UserKind(models.TextChoices):
    """Species of IAM principal stored in the swappable user table."""

    PERSON = "person", "Person"
    SERVICE = "service", "Service"


class UserQuerySet(AngeeQuerySet[Any]):
    """Queryset vocabulary for user-row surfaces."""

    def people(self) -> Any:
        """Return login-capable human users, excluding service-account rows."""

        return self.filter(kind=UserKind.PERSON)


class UserManager(AngeeManager.from_queryset(UserQuerySet), BaseUserManager):  # type: ignore[misc]
    """Manager for Angee's composed user model."""

    use_in_migrations = True

    def get_by_natural_key(self, username: str) -> Any:
        """Return a user for credential checks without row-scope filtering."""

        return self.system_context(reason="iam.credentials").get(**{self.model.USERNAME_FIELD: username})

    def get_for_session(self, user_id: Any) -> Any:
        """Return the session user through the named Django-auth reload seam."""

        return self.system_context(reason="iam.session").get(pk=user_id)

    async def aget_by_natural_key(self, username: str) -> Any:
        """Async sibling of ``get_by_natural_key``."""

        return await self.system_context(reason="iam.credentials").aget(**{self.model.USERNAME_FIELD: username})

    def create_user(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> Any:
        """Create and save a regular user."""

        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(username, email, password, **extra_fields)

    def create_superuser(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> Any:
        """Create and save a superuser."""

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(username, email, password, **extra_fields)

    def _create_user(
        self,
        username: str,
        email: str | None,
        password: str | None,
        **extra_fields: Any,
    ) -> Any:
        """Build, password-hash, and save one user."""

        if not username:
            raise ValueError("The given username must be set")
        user = self.model(
            username=self.model.normalize_username(username),
            email=self.normalize_email(email),
            **extra_fields,
        )
        user.set_password(password)
        actor = current_actor()
        user.sudo(reason="iam.user.create")
        user.save(using=self._db)
        if actor is not None:
            user.with_actor(actor)
        else:
            user.unsudo()
        return user


class User(SqidMixin, AbstractBaseUser, RebacPermissionsMixin, AngeeModel):
    """Abstract swappable user model composed into Angee runtimes.

    ``kind=service`` rows are non-login principals for agents and automation:
    they exist so audit and revision FKs can point at every actor species without
    widening password/OIDC login surfaces.
    """

    runtime = True

    sqid_prefix = "usr_"

    username_validator = UnicodeUsernameValidator()

    username = models.CharField(
        max_length=150,
        unique=True,
        validators=(username_validator,),
    )
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    kind = StateField(choices_enum=UserKind, default=UserKind.PERSON, db_index=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)
    preferences = models.JSONField(default=dict, blank=True)

    objects = UserManager()

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ("email",)

    class Meta:
        """Django model options for the IAM user source."""

        abstract = True
        swappable = "AUTH_USER_MODEL"
        rebac_resource_type = "auth/user"

    def clean(self) -> None:
        """Normalize username and email before validation."""

        super().clean()
        self.email = type(self).objects.normalize_email(self.email)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the user and mirror superuser status to the admin role."""

        update_fields = kwargs.get("update_fields")
        update_field_names = None
        if update_fields is not None:
            update_field_names = {update_fields} if isinstance(update_fields, str) else set(update_fields)
        if str(self.kind) == str(UserKind.SERVICE) and self.has_usable_password():
            self.set_unusable_password()
            if update_field_names is not None:
                update_field_names.add("password")
                kwargs["update_fields"] = update_field_names
        sync_admin_role = update_field_names is None or "is_superuser" in update_field_names
        super().save(*args, **kwargs)
        if not sync_admin_role:
            return
        role = app_settings.REBAC_UNIVERSAL_ADMIN_ROLE
        if not role:
            return
        if self.is_superuser:
            grant(actor=self, role=role)
        else:
            revoke(actor=self, role=role)

    def update_preferences(self, preferences: Mapping[str, Any]) -> None:
        """Replace this user's private UI preference object."""

        if not isinstance(preferences, Mapping):
            raise ValueError("preferences must be a JSON object")
        with system_context(reason="iam.preferences.update"), transaction.atomic():
            self.preferences = dict(preferences)
            self.save(update_fields=["preferences"])

    def get_full_name(self) -> str:
        """Return first and last name joined with a space."""

        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        """Return the user's short display name."""

        return self.first_name


COMPANY_MEMBER_RELATION = "direct_member"
"""REBAC relation naming a *direct* company-of-record membership grant.

Mirrors ``relation direct_member`` on the ``iam/company`` definition in
``permissions.zed``. Membership is stored as REBAC tuples, not an ORM join, and
the company owns that fact — so the relation name lives once here and
:meth:`CompanyManager.direct_memberships_of` reads it from the permission store.
"""


class CompanyQuerySet(ArchiveQuerySet[Any], AngeeQuerySet[Any]):
    """AngeeQuerySet plus the archive read vocabulary for companies of record."""


class CompanyManager(AngeeManager.from_queryset(CompanyQuerySet)):  # type: ignore[misc]
    """Manager for companies of record, adding the v1 default-company accessor."""

    def default(self) -> Company | None:
        """Return the company of record for single-company v1 reads.

        v1 semantics (multi-company selection UX is deferred — §3.7): the sole
        unarchived company, or the first by primary key when several exist.
        ``None`` until a company is provisioned — the framework ships none
        (worldwide-generic), so consumers seed their own.
        """

        return self.unarchived().order_by("pk").first()

    def direct_memberships_of(self, actor: Any) -> CompanyQuerySet:
        """Return the companies ``actor`` is a *direct* member of.

        Membership lives as REBAC ``direct_member`` tuples on ``iam/company``
        (:data:`COMPANY_MEMBER_RELATION`), so the answer is read from the
        permission store rather than an ORM join. The ``parent->member`` reach
        that lets an ancestor-company member act in a subsidiary is deliberately
        excluded — this is the direct grant, the fact
        :class:`CompanyScopedMixin` defaults a new row's company from. Archived
        companies are excluded (``unarchived()``): a defaulted company must be one
        the row can actually operate under, and an archived company is retired. The
        matched rows are returned scope-free (the membership tuple is itself the
        authorization), the same posture :meth:`Company.clean` uses for its
        integrity read; that scope-free read is expressed as a real
        :class:`CompanyQuerySet` in ``system_context`` rather than a plain base
        manager, so the archive vocabulary and company API stay in reach. The
        membership rows are read through :func:`active_relationship_model` and
        projected with ``values_list("resource_id")`` so the id column resolves in
        either backend storage mode (the registry translates it to its FK side).
        """

        subject = to_subject_ref(actor)
        id_attr = str(getattr(self.model._meta, "rebac_id_attr", None) or app_settings.REBAC_RESOURCE_ID_ATTR)
        company_ids = list(
            active_relationship_model()
            .objects.filter(
                resource_type=model_resource_type(self.model),
                relation=COMPANY_MEMBER_RELATION,
                subject_type=subject.subject_type,
                subject_id=subject.subject_id,
                optional_subject_relation=subject.optional_relation,
            )
            .values_list("resource_id", flat=True)
        )
        scope_free = cast(
            CompanyQuerySet,
            self.get_queryset().system_context(reason="direct company membership is itself the authorization"),
        )
        return scope_free.unarchived().filter(**{f"{id_attr}__in": company_ids})

    def co_member_subject_ids(self, actor: Any) -> list[str]:
        """Return the REBAC subject ids sharing a direct company membership with ``actor``.

        The colleague graph, read straight from the membership tuples the company
        owns: every :data:`COMPANY_MEMBER_RELATION` subject (``actor`` included) of
        the unarchived companies ``actor`` is itself a direct member of. Both hops
        stay in the permission store — the actor's companies via
        :meth:`direct_memberships_of` (which already excludes archived companies and
        the ancestor ``parent->member`` reach), then every direct member of those
        companies. Direct user membership only, mirroring
        :meth:`direct_memberships_of`; group-expanded members are out of scope. The
        shared-company tuple is itself the authorization, so a caller may read the
        matched users elevated — this returns only opaque subject ids, never a row.
        """

        id_attr = str(getattr(self.model._meta, "rebac_id_attr", None) or app_settings.REBAC_RESOURCE_ID_ATTR)
        company_ids = list(self.direct_memberships_of(actor).values_list(id_attr, flat=True))
        if not company_ids:
            return []
        return list(
            active_relationship_model()
            .objects.filter(
                resource_type=model_resource_type(self.model),
                relation=COMPANY_MEMBER_RELATION,
                resource_id__in=company_ids,
            )
            .values_list("subject_id", flat=True)
        )


class Company(AngeeDataModel, ArchiveMixin):
    """A company of record — the operating entity acting inside the system.

    An access-control scope, not a description of the outside world (an external
    customer/vendor is a ``parties.Organization``). Companies form a hierarchy
    through ``parent``, and REBAC lets an ancestor-company member reach every
    descendant scope (see ``permissions.zed`` ``iam/company``). Company-scoped
    role bindings live on the company row — a role is always *of a company*, never
    global — and are contributed additively by the consumer addon that owns each
    role (the ``permissions.extends.zed`` seam), so this framework model names no
    domain role. Carries no fiscal fields and no party FK — see the module
    docstring for the party/fiscal faces contributed downstream and the scope
    convention.
    """

    runtime = True

    sqid_prefix = "com_"

    name = models.CharField(max_length=200)
    parent = models.ForeignKey(
        "iam.Company",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )

    objects = CompanyManager()

    class Meta:
        """Django model options for the company-of-record source model."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "iam/company"
        rebac_id_attr = "sqid"
        constraints = (
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("id")),
                name="iam_company_parent_not_self",
            ),
        )

    def __str__(self) -> str:
        """Return the company's display name for Django displays."""

        return self.name

    def clean(self) -> None:
        """Reject a parent that is the company itself or forms an ancestor cycle.

        The ``parent`` hierarchy grants an ancestor-company member reach over every
        descendant, so a self-parent or a cycle would either brick the subtree with
        ``PermissionDepthExceeded`` on every check or loop the reach walk. The DB
        ``CheckConstraint`` owns the self case; this walk owns multi-hop cycles and
        runs on the write path (``full_clean``), so a schema-decoded ``parent`` edit
        that would close a loop fails with a clear ``ValidationError`` instead.
        """

        super().clean()
        if not self.parent_id:
            return
        if self.parent_id == self.pk:
            raise ValidationError({"parent": "A company cannot be its own parent."})
        manager = type(self)._base_manager
        seen: set[Any] = {self.pk} if self.pk is not None else set()
        ancestor_id: Any = self.parent_id
        while ancestor_id is not None:
            if ancestor_id in seen:
                raise ValidationError({"parent": "A company cannot be an ancestor of itself."})
            seen.add(ancestor_id)
            ancestor_id = manager.filter(pk=ancestor_id).values_list("parent_id", flat=True).first()


class CompanyScopedMixin(models.Model):
    """Scope a model's rows to one :class:`Company` of record.

    Contributes the ``company`` FK. Compose it on every model whose rows belong
    to a company, and add the matching ``company->…`` arm to the model's own
    ``permissions.zed`` definition (see the module docstring's scope convention),
    so isolation is enforced from day one.
    """

    company = models.ForeignKey(
        "iam.Company",
        on_delete=models.PROTECT,
        related_name="+",
        blank=True,
    )
    """The company of record that owns this row (see :class:`Company`).

    Never nullable — every persisted row belongs to exactly one company — but
    ``blank`` on input: when unset on create, :meth:`save` defaults it from the
    acting user's sole direct membership (§3.7).
    """

    class Meta:
        """Django model options for company-scope-only abstract inheritance."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Default an unset ``company`` from the acting user's sole membership.

        §3.7: ``company`` is an ordinary relation defaulted from the actor's
        membership. When unset, the row takes the acting user's single direct
        company membership; an ambiguous (several memberships) or
        actorless/anonymous write fails loudly with a field-named
        :class:`ValidationError` rather than guessing or inserting a null FK.
        """

        if self.company_id is None:
            self.company = self._default_company_from_membership()
        super().save(*args, **kwargs)

    def apply_create_defaults(self) -> Mapping[str, Sequence[Any]]:
        """Default a blank ``company`` before the create gate sees the row (§3.7).

        The auto-CRUD create preflight gates ``create = company->member`` on the
        unsaved instance, before :meth:`save` defaults the blank ``company`` — so
        without this the gate walks an absent company relation and fail-closes a
        create a single-membership member is entitled to. Applying the default
        here (idempotent with :meth:`save`, which then finds ``company`` already
        set) lets the gate evaluate against the company the row will persist. A
        caller-supplied ``company`` is left untouched and contributes nothing, so
        it still rides the caller's value through the gate (no default bypass);
        an ambiguous/actorless write still raises the field-named
        :class:`ValidationError` here rather than surfacing as a REBAC denial.
        """

        contributions = dict(super().apply_create_defaults())
        if self.company_id is None:
            self.company = self._default_company_from_membership()
            contributions["company"] = (self.company,)
        return contributions

    def _default_company_from_membership(self) -> Company:
        """Return the acting user's sole direct company, or raise naming ``company``."""

        actor = current_actor()
        if actor is None or is_anonymous_actor(actor):
            raise ValidationError(
                {"company": "Select a company: it cannot be defaulted for an unauthenticated actor."}
            )
        company_model = type(self)._meta.get_field("company").related_model
        memberships = list(company_model._default_manager.direct_memberships_of(actor)[:2])
        if len(memberships) == 1:
            return memberships[0]
        if not memberships:
            raise ValidationError(
                {"company": "Select a company: you are not a direct member of any company to default from."}
            )
        raise ValidationError(
            {"company": "Select a company: you are a direct member of several, so it cannot be defaulted."}
        )
