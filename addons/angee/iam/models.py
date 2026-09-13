"""Source models for Angee identity.

Pure identity: the swappable ``User`` and its manager. The OAuth connection
substrate (``OAuthClient``/``ExternalAccount``/``Credential``) is owned by
``integrate``; OIDC login fields are contributed onto that OAuth client by
``iam_integrate_oidc``. IAM's member-facing people directory is an
actor-scoped user queryset: REBAC read arms authorize rows, while the user
collection owns active-human filtering, search, ordering, and limits.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self, cast

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import Group as DjangoGroup
from django.contrib.auth.models import UnicodeUsernameValidator
from django.db import models, transaction
from django.db.models import Exists, OuterRef, Q, Subquery, TextField
from django.db.models.functions import Cast
from django.utils import timezone
from rebac import app_settings, current_actor, subject_id_attr, system_context
from rebac.mixins import RebacModelBase
from rebac.models import active_relationship_model
from rebac.permissions_mixin import RebacPermissionsMixin
from rebac.roles import ROLE_RELATION, grant, revoke

from angee.base.fields import StateField
from angee.base.identity import instance_from_public_id
from angee.base.mixins import SqidMixin
from angee.base.models import AngeeManager, AngeeModel, AngeeQuerySet
from angee.iam.identity import user_label

VISIBLE_PEOPLE_DEFAULT_LIMIT = 20
"""Default page size for member-facing people surfaces."""

VISIBLE_PEOPLE_MAX_LIMIT = 100
"""Upper bound a people-surface caller's ``limit`` is clamped to."""


class Group(DjangoGroup, metaclass=RebacModelBase):
    """Bind Django's existing group table to its native REBAC subject type.

    This tableless proxy is registered by Django app population. It has no
    ``runtime`` declaration: Django already owns the concrete group model.
    IAM's write backend owns admin mutation gates; read querysets use REBAC.
    """

    class Meta:
        """Native proxy identity used by REBAC lookup and subject resolution."""

        proxy = True
        app_label = "iam"
        rebac_resource_type = "auth/group"
        rebac_id_attr = "pk"


class UserKind(models.TextChoices):
    """Kind of IAM principal stored in the swappable user table."""

    PERSON = "person", "Person"
    SERVICE = "service", "Service"


class UserQuerySet(AngeeQuerySet[Any]):
    """Queryset vocabulary for user-row surfaces."""

    def people(self) -> Any:
        """Return login-capable human users, excluding service-account rows."""

        return self.filter(kind=UserKind.PERSON)

    def active_people(self) -> Self:
        """Return active human user rows."""

        return cast(Self, self.people().filter(is_active=True))

    def search_users(self, search: str) -> Self:
        """Filter users by the fields exposed by IAM identity pickers."""

        term = search.strip()
        if not term:
            return cast(Self, self)
        return cast(
            Self,
            self.filter(
                Q(username__icontains=term)
                | Q(first_name__icontains=term)
                | Q(last_name__icontains=term)
                | Q(email__icontains=term)
            ),
        )

    def ordered_users(self) -> Self:
        """Apply deterministic ordering using the swappable user's native fields."""

        concrete_fields = {field.name for field in self.model._meta.fields}
        fields: list[str] = []
        username_field = str(getattr(self.model, "USERNAME_FIELD", ""))
        if username_field in concrete_fields:
            fields.append(username_field)
        pk = self.model._meta.pk
        if pk is not None and pk.name not in fields:
            fields.append(pk.name)
        return cast(Self, self.order_by(*(fields or ["pk"])))

    def without_direct_roles(self, grant_rows: Any, role_resource_types: set[str]) -> Self:
        """Return users without direct role memberships in the installed schema."""

        attribute = subject_id_attr(self.model)
        subject_lookup = self.model._meta.pk.name if attribute == "pk" and self.model._meta.pk else attribute
        if subject_lookup == "sqid":
            subject_ids = tuple(
                dict.fromkeys(
                    str(subject_id)
                    for subject_id in grant_rows.values_list("subject_id", flat=True)
                    if subject_id
                )
            )
            assigned_user_pks = self.model._default_manager.filter(sqid__in=subject_ids).values("pk")
            return cast(Self, self.exclude(pk__in=Subquery(assigned_user_pks)))

        users = self.annotate(_iam_subject_id=Cast(subject_lookup, output_field=TextField()))
        assigned = active_relationship_model().objects.filter(
            resource_type__in=role_resource_types,
            relation=ROLE_RELATION,
            subject_type=app_settings.REBAC_USER_TYPE,
            subject_id=OuterRef("_iam_subject_id"),
            optional_subject_relation="",
        )
        return cast(Self, users.annotate(_iam_has_role=Exists(assigned)).filter(_iam_has_role=False))


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

    def visible_people(
        self,
        actor: Any,
        *,
        search: str = "",
        limit: int = VISIBLE_PEOPLE_DEFAULT_LIMIT,
    ) -> list[Any]:
        """Return actor-readable active people after search, ordering, and cap."""

        bounded = max(1, min(int(limit), VISIBLE_PEOPLE_MAX_LIMIT))
        queryset = self.with_actor(actor).active_people().search_users(search).ordered_users()
        return list(queryset[:bounded])

    def visible_person_from_public_id(self, actor: Any, public_id: str) -> Any | None:
        """Resolve one public user id against the same actor-scoped rows as the picker."""

        return instance_from_public_id(self.model, str(public_id), queryset=self.with_actor(actor).active_people())


class User(SqidMixin, AbstractBaseUser, RebacPermissionsMixin, AngeeModel):
    """Abstract swappable user model composed into Angee runtimes.

    ``kind=service`` rows are non-login principals for agents and automation:
    the same row owns authorization, audit stamps, and revision authorship
    without widening password/OIDC login surfaces.
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

    def __str__(self) -> str:
        """Use IAM's human label for person and service-user references alike."""

        return user_label(self)

    def get_full_name(self) -> str:
        """Return first and last name joined with a space."""

        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        """Return the user's short display name."""

        return self.first_name
