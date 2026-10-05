"""Source models for Angee identity.

Pure identity: the swappable ``User`` and its manager. The OAuth connection
substrate (``OAuthClient``/``ExternalAccount``/``Credential``) is owned by
``integrate``; OIDC login fields are contributed onto that OAuth client by
``iam_integrate_oidc``. IAM's member-facing people directory is an
actor-scoped user queryset: REBAC read arms authorize rows, while the user
collection owns active-human filtering, search, ordering, and limits.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Mapping
from copy import copy
from typing import Any, Self, cast

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.contrib.auth.models import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Exists, OuterRef, Q, TextField
from django.db.models.functions import Cast
from django.utils import timezone
from rebac import (
    PermissionDenied,
    SubjectRef,
    current_actor,
    resolve_subjects,
    system_context,
    to_subject_ref,
)
from rebac.memberships import grant as grant_membership
from rebac.memberships import revoke as revoke_membership
from rebac.permissions_mixin import RebacPermissionsMixin
from rebac.resources import model_resource_type
from rebac.roles import ROLE_RELATION

from angee.base.errors import DomainError
from angee.base.fields import StateField
from angee.base.identity import canonical_subject_ref, instance_from_public_id
from angee.base.mixins import OptimisticLockMixin
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet, role_anchor
from angee.iam.events import person_created
from angee.iam.identity import user_label
from angee.iam.roles import protected_account_holders

VISIBLE_PEOPLE_DEFAULT_LIMIT = 20
"""Default page size for member-facing people surfaces."""

VISIBLE_PEOPLE_MAX_LIMIT = 100
"""Upper bound a people-surface caller's ``limit`` is clamped to."""

VIEWABLE_PEOPLE_MAX_LIMIT = 25
"""Maximum preview results after SQL admission."""

IAMKind = role_anchor("iam/kind", name="IAMKind")
IAMProtected = role_anchor("iam/protected", name="IAMProtected")

PROTECTED_ACCOUNT_MESSAGE = (
    "This account is protected: it is your own, staff, a superuser, or holds an elevated role."
)
"""Refusal shared by every account verb and by generic updates of protected accounts."""

logger = logging.getLogger(__name__)


def bounded_limit(limit: int, *, maximum: int = VISIBLE_PEOPLE_MAX_LIMIT) -> int:
    """Clamp a people-picker limit to IAM's supported range."""

    return max(1, min(int(limit), maximum))


def _require_confirmed(confirmed: bool, message: str) -> None:
    """Refuse an account verb its caller did not explicitly confirm."""

    if confirmed is not True:
        raise ValidationError({"confirmed": message})


class Group(AngeeDataModel):
    """Named IAM principal set materialized into composed runtimes."""

    runtime = True
    sqid_prefix = "igr_"

    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        """Native group identity and default membership subject set."""

        abstract = True
        rebac_resource_type = "auth/group"
        rebac_id_attr = "pk"
        rebac_subject_relation = "member"

    def __str__(self) -> str:
        """Return the group name used by subject-label resolution."""

        return self.name

    @staticmethod
    def member_subject(value: str, *, require_existing: bool = True) -> SubjectRef:
        """Validate one canonical user subject accepted by group membership."""

        subject = canonical_subject_ref(value)
        if subject.subject_type != "auth/user" or subject.optional_relation:
            raise ValueError("Group members must use canonical 'auth/user:<id>' subjects.")
        if require_existing and subject not in resolve_subjects((subject,)):
            raise ValueError(f"Group member {value!r} was not found.")
        return subject

    def add_member(
        self,
        subject: str,
        *,
        caveat_name: str = "",
        caveat_context: Mapping[str, Any] | None = None,
    ) -> None:
        """Grant one existing user direct membership in this group."""

        if not self.has_access("write"):
            raise PermissionDenied("Write access to the IAM group is required.")
        with transaction.atomic():
            grant_membership(
                subject=self.member_subject(subject),
                container=self,
                caveat_name=caveat_name,
                caveat_context=caveat_context,
            )

    def remove_member(self, subject: str, *, caveat_name: str = "") -> bool:
        """Revoke an exact direct membership, allowing a stale user subject."""

        if not self.has_access("write"):
            raise PermissionDenied("Write access to the IAM group is required.")
        with transaction.atomic():
            return bool(
                revoke_membership(
                    subject=self.member_subject(subject, require_existing=False),
                    container=self,
                    caveat_name=caveat_name,
                )
            )


class UserKind(models.TextChoices):
    """Kind of IAM principal stored in the swappable user table."""

    PERSON = "person", "Person"
    SERVICE = "service", "Service"


class UserAccountAction(models.TextChoices):
    """Account verbs run on another person's account, each named by its Zed permission."""

    SET_ACTIVE = "set_active", "Set active"
    RENAME = "rename", "Rename"
    RESET_PASSWORD = "reset_password", "Reset password"
    ISSUE_PASSWORD = "issue_password", "Issue password"


class AccountExists(DomainError):
    """A person email is already claimed; never carries an account's details."""

    code = "ACCOUNT_EXISTS"


class AmbiguousAccountEmail(DomainError):
    """Multiple person accounts claim one email; carries no account details."""

    code = "ACCOUNT_EMAIL_AMBIGUOUS"


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

    def picker(self, search: str) -> Self:
        """Apply the common active-person search and ordering pipeline."""

        return self.active_people().search_users(search).ordered_users()

    def protection_condition(self, actor: Any = None) -> Q:
        """Match accounts whose authority others must not act on or assume.

        Staff, superusers and effective members of IAM's ``iam/protected`` Zed
        set — platform admins plus every elevated role a consumer unions into
        its ``member`` permission — are protected from every actor; an actor's
        own account is protected from that actor. Account verbs, generic
        updates by non-administrators and view-as share this one rule.
        """

        condition = Q(is_staff=True) | Q(is_superuser=True)
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


class UserManager(AngeeManager.from_queryset(UserQuerySet), BaseUserManager):  # type: ignore[misc]
    """Manager for Angee's composed user model."""

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        """Lowercase the whole address for every account kind, unlike Django.

        Django lowercases only the domain. Python owns whitespace stripping and
        Unicode lowercasing; the stored email is the key, with no SQL transform.
        ``bulk_create`` and queryset ``update()`` bypass normalization; callers
        writing emails through them must normalize first.
        """

        return (email or "").strip().lower()

    def _person_email_matches(self, email: str | None) -> Any:
        key = self.normalize_email(email)
        people = self.system_context(reason="iam.person.email_lookup").people()
        if not key:
            return people.none()
        return people.filter(email=key)

    def person_for_email(self, email: str) -> Any | None:
        """Look up one person by email, including inactive and staff accounts.

        This is a system identity lookup, never a public directory surface.
        No match returns None; ambiguous legacy matches raise a detail-free
        domain refusal so callers cannot confuse ambiguity with absence.
        """

        matches = list(self._person_email_matches(email).order_by("pk")[:2])
        if len(matches) > 1:
            raise AmbiguousAccountEmail
        return matches[0] if matches else None

    def person_email_collisions(self) -> dict[str, list[Any]]:
        """Group legacy person primary keys by Python-normalized nonempty email.

        The operator inventory reads only primary key and email through the base
        manager, without system scope or REBAC audit writes. Full user rows may
        require columns that the database has not yet migrated.
        """

        groups: dict[str, list[Any]] = {}
        rows = self.model._base_manager.filter(kind=UserKind.PERSON).order_by("pk")
        for pk, email in rows.values_list("pk", "email").iterator():
            key = self.normalize_email(email)
            if key:
                groups.setdefault(key, []).append(pk)
        return {key: groups[key] for key in sorted(groups) if len(groups[key]) > 1}

    def get_by_natural_key(self, username: str) -> Any:
        """Return a user for credential checks without row-scope filtering."""

        return self.system_context(reason="iam.credentials").get(**{self.model.USERNAME_FIELD: username})

    def get_for_session(self, user_id: Any) -> Any:
        """Return the session user through the named Django-auth reload seam."""

        return self.system_context(reason="iam.session").get(pk=user_id)

    async def aget_by_natural_key(self, username: str) -> Any:
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
    ) -> Any:
        """Create a non-staff person for the actor authorized by ``create``.

        The person event and its dependent identity link commit with the account.
        Omitting the password creates an account with no usable password.
        """

        actor = self.check_create()
        return self._create_person(
            username, email, password, first_name=first_name, last_name=last_name,
            actor=actor, reason="iam.person.create",
        )

    def create_person_as_system(self, username: str, email: str, *, reason: str) -> Any:
        """Create a passwordless person for a channel-authorized system ingress.

        The channel owner must require channel write and account create when
        enabling this ingress. The caller supplies its named reason. Nested
        writes and the person event run in the library's system context.
        """

        if not reason.strip():
            raise ValueError("System person creation requires a named reason.")
        if not self.normalize_email(email):
            raise ValidationError({"email": ["An email address is required."]})
        with system_context(reason=reason):
            user = self._create_person(
                username, email, None, first_name="", last_name="", actor=None, reason=reason,
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
    ) -> Any:
        """Own validation, uniqueness, persistence and the transactional event."""

        with transaction.atomic():
            if self._person_email_matches(email).exists():
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
            user.set_password(password)
            try:
                with transaction.atomic():
                    with system_context(reason="iam.person.validate"):
                        user.full_clean()
                    user.sudo(reason=reason).save()
            except (IntegrityError, ValidationError):
                if self._person_email_matches(email).exists():
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
    ) -> Any:
        """Create a user for trusted system paths such as bootstrap and OIDC.

        Actor-requested accounts use ``create_person`` and its creation gate.
        """

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
        user.save()
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

        return list(self.with_actor(actor).picker(search)[:bounded_limit(limit)])

    def visible_person_from_public_id(self, actor: Any, public_id: str) -> Any | None:
        """Resolve one public user id against the same actor-scoped rows as the picker."""

        return instance_from_public_id(self.model, str(public_id), queryset=self.with_actor(actor).active_people())

    def viewable_people(
        self,
        actor: Any,
        *,
        search: str = "",
        limit: int = VISIBLE_PEOPLE_DEFAULT_LIMIT,
    ) -> list[Any]:
        """Return permitted preview targets, applying admission before the cap."""

        bounded = bounded_limit(limit, maximum=VIEWABLE_PEOPLE_MAX_LIMIT)
        return [copy(person) for person in self.view_as_queryset(actor).search_users(search).ordered_users()[:bounded]]

    def view_as_queryset(self, actor: Any) -> UserQuerySet:
        """The single admission scope used by the picker and header lookup."""

        subject = to_subject_ref(actor)
        return self.with_actor(subject).with_action("view_as").preview_candidates(subject)

    def admit_view_as(self, actor: Any, public_id: str) -> Any | None:
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

    def active_person_for_subject(self, subject: SubjectRef) -> Any | None:
        """Resolve an accountable human actor from a concrete canonical subject."""

        if (
            subject.subject_type != model_resource_type(self.model)
            or subject.optional_relation
            or subject.subject_id in {"", "*"}
        ):
            return None
        try:
            return self.system_context(reason="iam.subject.active_person").active_people().filter(
                pk=subject.subject_id
            ).first()
        except (TypeError, ValueError, ValidationError):
            return None


class User(AbstractBaseUser, RebacPermissionsMixin, OptimisticLockMixin, AngeeDataModel):
    """Abstract swappable user model composed into Angee runtimes.

    ``kind=service`` rows are non-login principals for agents and automation:
    the same row owns authorization, audit stamps, and revision authorship
    without widening password/OIDC login surfaces. Every instance save counts
    a revision, so account verbs and administrative updates refuse a stale
    expected revision.
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
    # django-reversion's ``Revision.user`` reverse query name is ``revision``, so
    # the inherited save counter lives under another name; GraphQL still calls it ``revision``.
    revision = None
    account_revision = models.PositiveIntegerField(default=1, editable=False)
    REVISION_FIELD = "account_revision"

    objects = UserManager()

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ("email",)

    @property
    def is_person(self) -> bool:
        """Whether this principal represents a human, independently of activity."""

        return self.kind == UserKind.PERSON

    class Meta:
        """Django model options for the IAM user source."""

        abstract = True
        swappable = "AUTH_USER_MODEL"
        rebac_resource_type = "auth/user"
        constraints = [
            models.UniqueConstraint(
                fields=["email"],
                condition=Q(kind=UserKind.PERSON) & ~Q(email=""),
                name="iam_user_person_email_unique",
            ),
        ]

    def clean(self) -> None:
        """Normalize username and email before validation."""

        super().clean()
        self.email = type(self).objects.normalize_email(self.email)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Normalize email only when saving it; keep service principals passwordless.

        Partial saves excluding email neither load nor rewrite that field.
        ``bulk_create`` and queryset ``update()`` bypass this normalization;
        callers writing emails through them must normalize first.
        """

        update_fields = kwargs.get("update_fields")
        update_field_names = None
        if update_fields is not None:
            update_field_names = {update_fields} if isinstance(update_fields, str) else set(update_fields)
            kwargs["update_fields"] = update_field_names
        if update_field_names is None or "email" in update_field_names:
            self.email = type(self).objects.normalize_email(self.email)
        if str(self.kind) == str(UserKind.SERVICE) and self.has_usable_password():
            self.set_unusable_password()
            if update_field_names is not None:
                update_field_names.add("password")
                kwargs["update_fields"] = update_field_names
        super().save(*args, **kwargs)

    @staticmethod
    def account_action_blockers(action: str, protection: Q) -> tuple[tuple[Q, str], ...]:
        """Return the target conditions refusing ``action``, each with its refusal.

        ``protection`` is the acting subject's ``protection_condition``. The
        locked verbs and the batched ``account_actions`` projection share these
        conditions, so a control is offered exactly when its verb admits it.
        """

        usable_password = ~Q(password__startswith=UNUSABLE_PASSWORD_PREFIX)
        blockers = [
            (~Q(kind=UserKind.PERSON), "Only person accounts can be managed."),
            (protection, PROTECTED_ACCOUNT_MESSAGE),
        ]
        if action in {UserAccountAction.RESET_PASSWORD, UserAccountAction.ISSUE_PASSWORD}:
            blockers.append((Q(is_active=False), "Only active accounts can receive a password."))
        if action == UserAccountAction.RESET_PASSWORD:
            blockers.append((~usable_password, "This account has no password to reset; give it access instead."))
        if action == UserAccountAction.ISSUE_PASSWORD:
            blockers.append((usable_password, "This account already has a usable password."))
        return tuple(blockers)

    @classmethod
    def account_action_annotations(cls, actor: Any) -> dict[str, models.Expression]:
        """Batch every account verb's permission and eligibility for one actor's rows.

        Keys are ``UserAccountAction`` values. Without an actor nothing is allowed.
        """

        if actor is None:
            return {action: models.Value(False) for action in UserAccountAction.values}
        protection = cls.objects.protection_condition(actor)
        annotations: dict[str, models.Expression] = {}
        for action in UserAccountAction.values:
            rows = cls.objects.with_actor(actor).with_action(action).scoped_for_aggregate()
            for condition, _error in cls.account_action_blockers(action, protection):
                rows = rows.exclude(condition)
            annotations[action] = models.Exists(rows.filter(pk=models.OuterRef("pk")))
        return annotations

    def account_actions_for(self, actor: Any) -> list[str]:
        """Return the account verbs ``actor`` may run on this row, in declaration order."""

        if actor is None:
            return []
        annotations = type(self).account_action_annotations(actor)
        allowed = (
            type(self).objects.system_context(reason="iam.account.actions").filter(pk=self.pk)
            .annotate(**{f"_iam_{name}": expression for name, expression in annotations.items()})
            .values(*(f"_iam_{name}" for name in annotations)).first()
        ) or {}
        return [action for action in UserAccountAction.values if allowed.get(f"_iam_{action}")]

    def protected_against(self, actor: Any) -> bool:
        """Whether account verbs refuse this account for ``actor``; see ``protection_condition``."""

        users = type(self).objects
        return bool(
            users.system_context(reason="iam.account.protection").filter(pk=self.pk)
            .filter(users.protection_condition(actor)).exists()
        )

    def account_action_error(self, action: str, actor: Any) -> str | None:
        """Return the first refusal of ``action`` on this stored row for ``actor``, or None."""

        users = type(self).objects
        rows = users.system_context(reason="iam.account.eligibility").filter(pk=self.pk)
        for condition, error in type(self).account_action_blockers(action, users.protection_condition(actor)):
            if rows.filter(condition).exists():
                return error
        return None

    def _lock_for_account_action(self, action: str, *, expected_revision: int | None) -> Self:
        """Lock this account and admit ``action`` for the instance's actor under the lock.

        Actor presence is required before locking. System callers bypass the
        permission by the REBAC library's contract but still meet eligibility;
        protection then excludes staff, superusers and elevated accounts only.
        """

        actor, bypass = self.effective_actor(strict=True)
        user = type(self).objects.system_context(reason=f"iam.account.{action}").lock_if_supported().get(pk=self.pk)
        if actor is not None:
            user.with_actor(actor)
        elif bypass:
            user.sudo(reason=f"iam.account.{action}")
        if not user.has_access(action):
            raise PermissionDenied(f"The {action} permission on this account is required.")
        if expected_revision is not None:
            user.require_revision(expected_revision)
        if error := user.account_action_error(action, actor):
            raise ValidationError(error)
        return cast(Self, user)

    def _store_new_password(self, user: Any, *, reason: str, expected_revision: int | None) -> str:
        """Hash a fresh random secret onto the locked ``user`` and return it once."""

        password = secrets.token_urlsafe(24)
        user.set_password(password)
        user.sudo(reason=reason).save(update_fields=["password", "updated_at"], expected_revision=expected_revision)
        self.password = user.password
        self.account_revision = user.account_revision
        return password

    def issue_password(self) -> str:
        """Set and return one random credential for a passwordless, unprotected active person.

        Permission and eligibility are checked on the locked row. Only the hash
        is stored; later calls refuse, including calls through a stale instance.
        """

        with transaction.atomic():
            user = self._lock_for_account_action(str(UserAccountAction.ISSUE_PASSWORD), expected_revision=None)
            return self._store_new_password(user, reason="iam.password.issue", expected_revision=None)

    def reset_password(self, *, confirmed: bool, expected_revision: int) -> str:
        """Replace an active, unprotected person's usable password and return the new secret once.

        The previous password stops working and existing sessions lose their
        authentication hash. Only the new hash is stored.
        """

        _require_confirmed(confirmed, "Confirm resetting this account's password.")
        with transaction.atomic():
            action = str(UserAccountAction.RESET_PASSWORD)
            user = self._lock_for_account_action(action, expected_revision=expected_revision)
            return self._store_new_password(user, reason="iam.password.reset", expected_revision=expected_revision)

    def set_active(self, active: bool, *, confirmed: bool, expected_revision: int) -> None:
        """Deactivate or reactivate an unprotected person account.

        A deactivated account can no longer sign in, its sessions stop loading
        and it leaves the active-people pickers. Setting the current state is a
        no-op that still checks the expected revision.
        """

        _require_confirmed(confirmed, "Confirm changing this account's access.")
        with transaction.atomic():
            user = self._lock_for_account_action(str(UserAccountAction.SET_ACTIVE), expected_revision=expected_revision)
            if user.is_active != active:
                user.is_active = active
                user.sudo(reason="iam.account.set_active").save(
                    update_fields=["is_active", "updated_at"], expected_revision=expected_revision,
                )
            self.is_active, self.account_revision = user.is_active, user.account_revision

    def rename(self, *, first_name: str, last_name: str, confirmed: bool, expected_revision: int) -> None:
        """Change only an unprotected person's name fields; username and email never change here."""

        _require_confirmed(confirmed, "Confirm renaming this account.")
        first_name, last_name = " ".join(first_name.split()), " ".join(last_name.split())
        if not (first_name or last_name):
            raise ValidationError({"first_name": "Enter a first or last name."})
        with transaction.atomic():
            user = self._lock_for_account_action(str(UserAccountAction.RENAME), expected_revision=expected_revision)
            user.first_name, user.last_name = first_name, last_name
            user.clean_fields(exclude=[
                field.name for field in user._meta.fields if field.name not in {"first_name", "last_name"}
            ])
            user.sudo(reason="iam.account.rename").save(
                update_fields=["first_name", "last_name", "updated_at"], expected_revision=expected_revision,
            )
            self.first_name, self.last_name = user.first_name, user.last_name
            self.account_revision = user.account_revision

    def update_account(
        self,
        changes: Mapping[str, Any],
        *,
        password: str | None = None,
        expected_revision: int | None = None,
    ) -> None:
        """Apply an administrative patch through the instance actor's ``write`` and field gates.

        Only changed fields are saved, so each ``write__<field>`` gate guards a
        real change. A protected account accepts the patch only from an actor
        holding ``administer`` on it: a consumer's ``write`` grant never edits
        an elevated account, the actor's own included.
        """

        actor, _bypass = self.effective_actor(strict=True)
        if expected_revision is not None:
            self.require_revision(expected_revision)
        if actor is not None and self.protected_against(actor) and not self.has_access("administer"):
            raise ValidationError(PROTECTED_ACCOUNT_MESSAGE)
        update_fields = {field for field, value in changes.items() if getattr(self, field) != value}
        for field in update_fields:
            setattr(self, field, changes[field])
        if password:
            self.set_password(password)
            update_fields.add("password")
        self.full_clean()
        if update_fields:
            self.save(update_fields=[*sorted(update_fields), "updated_at"], expected_revision=expected_revision)

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
