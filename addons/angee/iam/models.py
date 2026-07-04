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
policy) is a same-row ``extends`` merge owned by ``arp.accounting`` — both
downstream, so the dependency stays one-way.

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

Company-scoped role bindings are relations *on the company* (``accountant``,
``salesperson``), so a scoped resource reads ``company->accountant`` — an
accountant *of company A*, never a global accountant. Subsidiaries inherit reach
through ``parent`` (``permission member = direct_member + parent->member``): an
ancestor-company member reaches every descendant scope.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import UnicodeUsernameValidator
from django.db import models, transaction
from django.utils import timezone
from rebac import app_settings, current_actor, system_context
from rebac.permissions_mixin import RebacPermissionsMixin
from rebac.roles import grant, revoke

from angee.base.mixins import ArchiveMixin, ArchiveQuerySet, SqidMixin
from angee.base.models import AngeeDataModel, AngeeManager, AngeeModel, AngeeQuerySet


class UserManager(AngeeManager, BaseUserManager):
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
    """Abstract swappable user model composed into Angee runtimes."""

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
        sync_admin_role = update_fields is None or "is_superuser" in update_fields
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


class Company(AngeeDataModel, ArchiveMixin):
    """A company of record — the operating entity acting inside the system.

    An access-control scope, not a description of the outside world (an external
    customer/vendor is a ``parties.Organization``). Companies form a hierarchy
    through ``parent``, and REBAC lets an ancestor-company member reach every
    descendant scope (see ``permissions.zed`` ``iam/company``). Company-scoped
    role bindings (accountant, salesperson) live on the company row, so a role is
    always *of a company*, never global. Carries no fiscal fields and no party
    FK — see the module docstring for the party/fiscal faces contributed
    downstream and the scope convention.
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

    def __str__(self) -> str:
        """Return the company's display name for Django displays."""

        return self.name


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
    )
    """The company of record that owns this row (see :class:`Company`)."""

    class Meta:
        """Django model options for company-scope-only abstract inheritance."""

        abstract = True
