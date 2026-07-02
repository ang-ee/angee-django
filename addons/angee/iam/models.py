"""Source models for Angee identity.

Pure identity: the swappable ``User`` and its manager. The OAuth connection
substrate (``OAuthClient``/``ExternalAccount``/``Credential``) is owned by
``integrate``; OIDC login fields are contributed onto that OAuth client by
``iam_integrate_oidc``.
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

from angee.base.mixins import SqidMixin
from angee.base.models import AngeeManager, AngeeModel


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
