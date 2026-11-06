"""Native account factories and trusted authentication lookups."""

from __future__ import annotations

import logging
import re
from copy import copy
from typing import TYPE_CHECKING, Any, cast

from django.contrib.auth.base_user import BaseUserManager
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, router, transaction
from rebac import (
    ObjectRef,
    PermissionDenied,
    SubjectRef,
    app_settings,
    check_new,
    current_actor,
    database_context,
    system_context,
)
from rebac.errors import MissingActorError
from rebac.managers import RebacManager
from rebac.memberships import grant as grant_membership
from rebac.resources import model_resource_type

from angee.base.models.identity import instance_from_public_id
from angee.base.models.persistence import require_composed_type
from angee.iam.choices import UserKind
from angee.iam.events import person_created
from angee.iam.exceptions import AccountExists, AmbiguousAccountEmail
from angee.iam.querysets import ProtectedQuerySet, ProtectedUnscopedQuerySet, UserQuerySet

if TYPE_CHECKING:
    from angee.iam.models import User


logger = logging.getLogger(__name__)

VISIBLE_PEOPLE_DEFAULT_LIMIT = 20
"""Default page size for member-facing people surfaces."""

VISIBLE_PEOPLE_MAX_LIMIT = 100
"""Upper bound a people-surface caller's ``limit`` is clamped to."""

VIEWABLE_PEOPLE_MAX_LIMIT = 25
"""Maximum preview results after SQL admission."""


def bounded_limit(limit: int, *, maximum: int = VISIBLE_PEOPLE_MAX_LIMIT) -> int:
    """Clamp a people-picker limit to IAM's supported range."""

    return max(1, min(int(limit), maximum))


_ProtectedManagerBase = RebacManager.from_queryset(ProtectedQuerySet)


class ProtectedManager[T: models.Model](_ProtectedManagerBase[T]):
    """Manager retaining the native REBAC collection contract."""

    model: type[T]

    def get_queryset(self) -> ProtectedQuerySet[T]:
        """Retain the protected queryset promised by this manager factory."""

        return require_composed_type(super().get_queryset(), ProtectedQuerySet)


_ProtectedUnscopedManagerBase = models.Manager.from_queryset(ProtectedUnscopedQuerySet)


class ProtectedUnscopedManager[T: models.Model](_ProtectedUnscopedManagerBase[T]):
    """Manager preserving unfiltered reads and native tracked writes."""

    model: type[T]


_UserManagerBase = ProtectedManager.from_queryset(UserQuerySet)


class UserManager[UserT: User](_UserManagerBase[UserT], BaseUserManager[UserT]):
    """Manager for Angee's composed user model."""

    use_in_migrations = True

    def get_queryset(self) -> UserQuerySet[UserT]:
        """Retain user scopes after REBAC pins a fresh queryset."""

        return require_composed_type(super().get_queryset(), UserQuerySet)

    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        """Lowercase the whole address for every account kind, unlike Django.

        Django lowercases only the domain. Python owns whitespace stripping and
        Unicode lowercasing; the stored email is the key, with no SQL transform.
        ``bulk_create`` and queryset ``update()`` bypass normalization; callers
        writing emails through them must normalize first.
        """

        return (email or "").strip().lower()

    def _person_email_matches(self, email: str | None) -> UserQuerySet[UserT]:
        key = self.normalize_email(email)
        people = self.system_context(reason="iam.person.email_lookup").people()
        if not key:
            return people.none()
        return people.filter(email=key)

    def person_for_email(self, email: str) -> UserT | None:
        """Look up one person by email, including inactive and staff accounts.

        This is a system identity lookup, never a public directory surface.
        No match returns None; ambiguous legacy matches raise a detail-free
        domain refusal so callers cannot confuse ambiguity with absence.
        """

        matches = list(self._person_email_matches(email).order_by("pk")[:2])
        if len(matches) > 1:
            raise AmbiguousAccountEmail
        return matches[0] if matches else None

    def get_by_natural_key(self, username: str | None) -> UserT:
        """Return a user for credential checks without row-scope filtering."""

        return self.system_context(reason="iam.credentials").get(**{self.model.USERNAME_FIELD: username})

    def get_for_session(self, user_id: Any) -> UserT:
        """Reload the session principal by identity before an actor context exists.

        Django's authentication backend calls this with the signed session's
        canonical user id. It is identity bootstrap, not an application row
        read, so the declared unscoped base manager is the owning read path.
        """

        return self.model._base_manager.db_manager(self.db).get(pk=user_id)

    async def aget_by_natural_key(self, username: str | None) -> UserT:
        """Async sibling of ``get_by_natural_key``."""

        return await self.system_context(reason="iam.credentials").aget(**{self.model.USERNAME_FIELD: username})

    def create_person(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        *,
        first_name: str = "",
        last_name: str = "",
    ) -> UserT:
        """Create a non-staff person for the actor authorized by ``create``.

        The person event and its dependent identity link commit with the account.
        Omitting the password creates an account with no usable password.
        """

        actor, _ = self.effective_actor(strict=True)
        resource_type = model_resource_type(self.model)
        if actor is None:
            raise MissingActorError(f"Creating {resource_type or self.model._meta.label_lower} requires an actor.")
        if (
            resource_type is None
            or not check_new(
                subject=actor,
                action="create",
                resource_type=resource_type,
            ).allowed
        ):
            raise PermissionDenied(f"Denied: {actor} cannot create {resource_type or self.model._meta.label_lower}")
        return self._create_person(
            username,
            email,
            password,
            first_name=first_name,
            last_name=last_name,
            actor=actor,
            reason="iam.person.create",
        )

    def create_person_as_system(self, username: str, email: str, *, reason: str) -> UserT:
        """Create a passwordless person for a channel-authorized system ingress.

        The channel owner must require channel write and account create when
        enabling this ingress. The caller supplies its named reason. Nested
        writes and the person event run in the library's system context.
        """

        if not reason.strip():
            raise ValueError("System person creation requires a named reason.")
        if not self.normalize_email(email):
            raise ValidationError({"email": ["An email address is required."]})
        using = self._db or router.db_for_write(self.model)
        with database_context(using), system_context(reason=reason):
            user = self._create_person(
                username,
                email,
                None,
                first_name="",
                last_name="",
                actor=None,
                reason=reason,
            )
        logger.info("Created person account %s as system: %s", user.pk, reason)
        return user

    def _create_person(
        self,
        username: str,
        email: str | None,
        password: str | None,
        *,
        first_name: str,
        last_name: str,
        actor: SubjectRef | None,
        reason: str,
    ) -> UserT:
        """Own validation, uniqueness, persistence and the transactional event."""

        using = self._db or router.db_for_write(self.model)
        with database_context(using), transaction.atomic(using=using):
            if self.db_manager(using)._person_email_matches(email).exists():
                raise AccountExists
            user = self.model(
                username=self.model.normalize_username(username),
                email=self.normalize_email(email),
                first_name=first_name,
                last_name=last_name,
                kind=UserKind.PERSON,
                is_staff=False,
                is_superuser=False,
                is_active=True,
            )
            user._state.db = using
            user.set_password(password)
            try:
                with transaction.atomic(using=using):
                    with system_context(reason="iam.person.validate"):
                        user.full_clean(validate_unique=False)
                        # Django's unique-field checks select their own read alias;
                        # this factory's candidate must be checked on its write alias.
                        for field in user._meta.concrete_fields:
                            if not field.unique or field.primary_key:
                                continue
                            value = field.value_from_object(user)
                            if value is None:
                                continue
                            if type(user)._default_manager.using(using).filter(**{field.name: value}).exists():
                                raise ValidationError({
                                    field.name: [user.unique_error_message(type(user), (field.name,))],
                                })
                    user.sudo(reason=reason).save(using=using)
            except IntegrityError, ValidationError:
                if self.db_manager(using)._person_email_matches(email).exists():
                    raise AccountExists from None
                raise
            if actor is not None:
                user.with_actor(actor)
            else:
                user.unsudo()
            person_created.send(sender=self.model, instance=user)
        return user

    def create_user(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> UserT:
        """Create a user for trusted system paths such as bootstrap and OIDC.

        Actor-requested accounts use ``create_person`` and its creation gate.
        """

        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)

        return self._create_user(username, email, password, **extra_fields)

    def create_user_from_seed(
        self,
        seed: str,
        *,
        fallback: str,
        email: str = "",
        first_name: str = "",
        last_name: str = "",
    ) -> UserT:
        """Create a passwordless person from a trusted identity's proposed name.

        The owning protocol admits the identity and handles collision failures.
        This factory owns username sanitation, length and available suffixes;
        it never grants staff or superuser flags or infers verified email.
        """

        maximum = cast(int, self.model._meta.get_field("username").max_length)
        base = re.sub(r"[^\w.@+-]", "-", seed).strip("-")[: maximum - 10] or fallback
        candidate = base
        suffix = 1
        using = self._db or router.db_for_write(self.model)
        manager = self.db_manager(using)
        with database_context(using), system_context(reason="iam.user.seed"), transaction.atomic(using=using):
            rows = manager.get_queryset()
            while rows.filter(username=candidate).exists():
                suffix_text = f"-{suffix}"
                candidate = f"{base[: maximum - len(suffix_text)]}{suffix_text}"
                suffix += 1
            return manager.create_user(
                candidate,
                email=email,
                password=None,
                first_name=first_name,
                last_name=last_name,
                is_staff=False,
                is_superuser=False,
            )

    def ensure_service_user(self, *, username: str, name: str, user_id: Any = None) -> UserT:
        """Create or relabel a saved owner's stable, non-login principal.

        Call after the owning record's write admission. The caller owns its
        foreign key and stable username; this factory owns all User mutations.
        """

        using = self._db or router.db_for_write(self.model)
        with database_context(using), system_context(reason="iam.service_user.sync"), transaction.atomic(using=using):
            rows = self.db_manager(using).get_queryset()
            if user_id is None:
                user, created = rows.get_or_create(
                    username=username,
                    defaults=self.model.service_identity_values(username=username, name=name),
                )
                if created:
                    return user
                user_id = user.pk
            user = rows.lock_if_supported(no_key=True).get(pk=user_id)
            user.update_service_identity(username=username, name=name)
            return user

    def create_superuser(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> UserT:
        """Create and save a superuser."""

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(username, email, password, **extra_fields)

    def ensure_bootstrap_admin(
        self,
        username: str,
        *,
        email: str | None = None,
        password: str | None = None,
    ) -> tuple[UserT, bool]:
        """Atomically create or promote the initial admin and grant its platform role.

        Uses this manager's database for the account, REBAC schema, membership
        and audit writes. Existing usable passwords and non-empty emails survive
        repeated provisioning. A new account requires a password; an existing
        passwordless account receives one only when supplied.
        """

        if not username:
            raise ValueError("bootstrap_admin requires a username.")
        role = app_settings.REBAC_UNIVERSAL_ADMIN_ROLE
        if not role:
            raise ValueError("bootstrap_admin requires REBAC_UNIVERSAL_ADMIN_ROLE.")
        using = self._db or router.db_for_write(self.model)
        manager = self.db_manager(using)
        with database_context(using), system_context(reason="iam.bootstrap_admin"), transaction.atomic(using=using):
            try:
                user = manager.system_context(reason="iam.bootstrap_admin").get(
                    **{self.model.USERNAME_FIELD: username},
                )
            except self.model.DoesNotExist:
                if not password:
                    raise ValueError(
                        "bootstrap_admin needs ANGEE_BOOTSTRAP_ADMIN_PASSWORD or --password when creating a new admin."
                    )
                user = manager.create_superuser(username=username, email=email, password=password)
                created = True
            else:
                created = False
                update_fields = set()
                if email and not user.email:
                    user.email = self.normalize_email(email)
                    update_fields.add("email")
                for field in ("is_active", "is_staff", "is_superuser"):
                    if getattr(user, field) is not True:
                        setattr(user, field, True)
                        update_fields.add(field)
                if password and not user.has_usable_password():
                    user.set_password(password)
                    update_fields.add("password")
                if update_fields:
                    user.save(using=using, update_fields=sorted(update_fields))
            grant_membership(subject=user, container=ObjectRef.parse(role))
        return user, created

    def _create_user(
        self,
        username: str,
        email: str | None,
        password: str | None,
        **extra_fields: Any,
    ) -> UserT:
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
    ) -> list[UserT]:
        """Return ``actor``'s active colleagues after search, ordering, and cap."""

        return list(self.colleagues(actor).picker(search)[: bounded_limit(limit)])

    def viewable_people(
        self,
        actor: Any,
        *,
        search: str = "",
        limit: int = VISIBLE_PEOPLE_DEFAULT_LIMIT,
    ) -> list[UserT]:
        """Return permitted preview targets, applying admission before the cap."""

        bounded = bounded_limit(limit, maximum=VIEWABLE_PEOPLE_MAX_LIMIT)
        return [copy(person) for person in self.view_as_queryset(actor).search_users(search).ordered_users()[:bounded]]

    def admit_view_as(self, actor: Any, public_id: str) -> UserT | None:
        """Admit a preview identity for both the picker and GraphQL header.

        Admission uses an actor-scoped candidate. REBAC's native copy contract
        strips its instance actor/elevation before it becomes a request identity;
        subsequent instance checks therefore follow the preview's ambient actor.
        """

        candidate = instance_from_public_id(
            self.model,
            str(public_id),
            queryset=self.view_as_queryset(actor),
        )
        return copy(candidate) if candidate is not None else None
