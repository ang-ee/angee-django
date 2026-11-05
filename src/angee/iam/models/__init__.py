"""Source models for Angee identity.

Pure identity: the swappable ``User`` and its manager. The OAuth connection
substrate (``OAuthClient``/``ExternalAccount``/``Credential``) is owned by
``integrate``; OIDC login fields are contributed onto that OAuth client by
``iam_integrate_oidc``. Everyone signed in reads a person's name; restricted
fields are ``read__<field>`` gates for people managers and the person. IAM's
people directory (``list``) and pickers (``colleague``) are actor-scoped user
querysets: REBAC arms authorize rows, while the user collection owns
active-human filtering, search, ordering, and limits.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any, ClassVar, Self, cast

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from rebac import (
    PermissionDenied,
    SubjectRef,
    database_context,
    resolve_subjects,
    system_context,
    to_object_ref,
    to_subject_ref,
    user_subject_type,
)
from rebac.evaluator import current_evaluator, evaluator_scope
from rebac.memberships import grant as grant_membership
from rebac.memberships import revoke as revoke_membership
from rebac.models import active_relationship_model
from rebac.permissions_mixin import RebacPermissionsMixin
from rebac.roles import ROLE_RELATION

from angee.base.fields.state import StateField
from angee.base.models.identity import PublicIdentity
from angee.base.models.locks import OptimisticLockMixin
from angee.iam.choices import UserAccountAction as UserAccountAction
from angee.iam.choices import UserKind as UserKind
from angee.iam.exceptions import AccountExists as AccountExists
from angee.iam.exceptions import AmbiguousAccountEmail as AmbiguousAccountEmail
from angee.iam.identity import canonical_subject_ref, user_label
from angee.iam.managers import VIEWABLE_PEOPLE_MAX_LIMIT as VIEWABLE_PEOPLE_MAX_LIMIT
from angee.iam.managers import VISIBLE_PEOPLE_DEFAULT_LIMIT as VISIBLE_PEOPLE_DEFAULT_LIMIT
from angee.iam.managers import VISIBLE_PEOPLE_MAX_LIMIT as VISIBLE_PEOPLE_MAX_LIMIT
from angee.iam.managers import UserManager as UserManager
from angee.iam.managers import bounded_limit as bounded_limit
from angee.iam.models.append_only import IAMAppendOnlyMixin as IAMAppendOnlyMixin
from angee.iam.models.append_only import ProtectedAppendOnlyModel as ProtectedAppendOnlyModel
from angee.iam.models.base import IAMProtectionMixin as IAMProtectionMixin
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.base import ProtectedModel as ProtectedModel
from angee.iam.models.locks import IAMRowLockMixin as IAMRowLockMixin
from angee.iam.models.locks import ProtectedRowLockMixin as ProtectedRowLockMixin
from angee.iam.models.merge import IAMMergeableMixin as IAMMergeableMixin
from angee.iam.models.merge import ProtectedMergeableMixin as ProtectedMergeableMixin
from angee.iam.models.refs import IAMRecordRefMixin as IAMRecordRefMixin
from angee.iam.models.refs import ProtectedRecordRefMixin as ProtectedRecordRefMixin
from angee.iam.models.roles import role_anchor
from angee.iam.models.trash import IAMTrashMixin as IAMTrashMixin
from angee.iam.models.trash import ProtectedTrashMixin as ProtectedTrashMixin
from angee.iam.querysets import UserQuerySet as UserQuerySet

IAMKind = role_anchor("iam/kind", name="IAMKind")
IAMProtected = role_anchor("iam/protected", name="IAMProtected")

PROTECTED_ACCOUNT_MESSAGE = "This account is protected: it is your own, staff, a superuser, or holds an elevated role."
"""Refusal shared by every account verb and by generic updates of protected accounts."""


def _require_confirmed(confirmed: bool, message: str) -> None:
    """Refuse an account verb its caller did not explicitly confirm."""

    if confirmed is not True:
        raise ValidationError({"confirmed": message})


class Group(ProtectedDataModel, concrete=True):
    """Named IAM principal set materialized into composed runtimes."""

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

    def member_rows(self) -> QuerySet[Any]:
        """Select direct membership tuples for this group's canonical identity."""

        ref = to_object_ref(self)
        return (
            active_relationship_model()
            .objects.using(self._state.db)
            .filter(
                resource_type=ref.resource_type,
                resource_id=ref.resource_id,
                relation=ROLE_RELATION,
            )
            .order_by_subject()
        )

    def binding_rows(self) -> QuerySet[Any]:
        """Select bindings addressed to this group's declared member subject set."""

        subject = to_subject_ref(self)
        return (
            active_relationship_model()
            .objects.using(self._state.db)
            .filter(
                subject_type=subject.subject_type,
                subject_id=subject.subject_id,
                optional_subject_relation=subject.optional_relation,
            )
            .order_by_resource()
        )

    @staticmethod
    def member_subject(value: str, *, require_existing: bool = True) -> SubjectRef:
        """Validate one canonical user subject accepted by group membership."""

        subject = canonical_subject_ref(value)
        if subject.subject_type != user_subject_type() or subject.optional_relation:
            raise ValueError(f"Group members must use canonical '{user_subject_type()}:<id>' subjects.")
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


class User(AbstractBaseUser, RebacPermissionsMixin, OptimisticLockMixin, ProtectedDataModel, concrete=True):
    """Abstract swappable user model composed into Angee runtimes.

    ``kind=service`` rows are non-login principals for agents and automation:
    the same row owns authorization, audit stamps, and revision authorship
    without widening password/OIDC login surfaces. Every instance save counts
    a revision, so account verbs and administrative updates refuse a stale
    expected revision.
    """

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
    # The user save counter is stored as ``account_revision``; GraphQL exposes it as ``revision``.
    revision = None
    account_revision = models.PositiveIntegerField(default=1, editable=False)
    REVISION_FIELD = "account_revision"

    objects = UserManager()

    EMAIL_FIELD = "email"
    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

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
    def account_action_blockers(action: str, protection: Q, inactive: Q) -> tuple[tuple[Q, str], ...]:
        """Return the target conditions refusing ``action``, each with its refusal.

        ``protection`` and ``inactive`` are the acting subject's
        ``protection_condition`` and ``inactive_condition``. The locked verbs and
        the batched ``account_actions`` projection share these conditions, so a
        control is offered exactly when its verb admits it.
        """

        usable_password = ~Q(password__startswith=UNUSABLE_PASSWORD_PREFIX)
        blockers = [
            (~Q(kind=UserKind.PERSON), "Only person accounts can be managed."),
            (protection, PROTECTED_ACCOUNT_MESSAGE),
        ]
        if action in {UserAccountAction.RESET_PASSWORD, UserAccountAction.ISSUE_PASSWORD}:
            blockers.append((inactive, "Only active accounts can receive a password."))
        if action == UserAccountAction.RESET_PASSWORD:
            blockers.append((~usable_password, "This account has no password to reset; give it access instead."))
        if action == UserAccountAction.ISSUE_PASSWORD:
            blockers.append((usable_password, "This account already has a usable password."))
        return tuple(blockers)

    @classmethod
    def account_action_annotations(cls, actor: Any) -> dict[str, models.expressions.BaseExpression]:
        """Batch every account verb's permission and eligibility for one actor's rows.

        Keys are ``UserAccountAction`` values. Without an actor nothing is allowed.
        """

        if actor is None:
            return {action: models.Value(False) for action in UserAccountAction.values}
        protection = cls.objects.protection_condition(actor)
        inactive = cls.objects.inactive_condition(actor)
        annotations: dict[str, models.expressions.BaseExpression] = {}
        for action in UserAccountAction.values:
            rows = cls.objects.with_actor(actor).with_action(action).scoped_for_aggregate()
            for condition, _error in cls.account_action_blockers(action, protection, inactive):
                rows = rows.exclude(condition)
            annotations[action] = models.Exists(rows.filter(pk=models.OuterRef("pk")))
        return annotations

    def account_actions_for(self, actor: Any) -> list[str]:
        """Return allowed verbs, sharing one native policy snapshot for a direct call."""

        if actor is None:
            return []
        with evaluator_scope() if current_evaluator() is None else nullcontext():
            annotations = type(self).account_action_annotations(actor)
            allowed = (
                type(self)
                ._base_manager
                .filter(pk=self.pk)
                .annotate(**{f"_iam_{name}": expression for name, expression in annotations.items()})
                .values(*(f"_iam_{name}" for name in annotations))
                .first()
            ) or {}
        return [action for action in UserAccountAction.values if allowed.get(f"_iam_{action}")]

    def protected_against(self, actor: Any) -> bool:
        """Whether account verbs refuse this account for ``actor``; see ``protection_condition``."""

        users = type(self).objects
        return bool(
            users.system_context(reason="iam.account.protection")
            .filter(pk=self.pk)
            .filter(users.protection_condition(actor))
            .exists()
        )

    def account_action_error(self, action: str, actor: Any) -> str | None:
        """Return the first refusal of ``action`` on this stored row for ``actor``, or None."""

        users = type(self).objects
        rows = users.system_context(reason="iam.account.eligibility").filter(pk=self.pk)
        blockers = type(self).account_action_blockers(
            action,
            users.protection_condition(actor),
            users.inactive_condition(actor),
        )
        for condition, error in blockers:
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
                    update_fields=["is_active", "updated_at"],
                    expected_revision=expected_revision,
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
            user.clean_fields(
                exclude=[field.name for field in user._meta.fields if field.name not in {"first_name", "last_name"}]
            )
            user.sudo(reason="iam.account.rename").save(
                update_fields=["first_name", "last_name", "updated_at"],
                expected_revision=expected_revision,
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

    @staticmethod
    def service_identity_values(*, username: str, name: str) -> dict[str, Any]:
        """The user facts derived from a service-owning record."""

        return {"username": username, "first_name": name, "last_name": "", "email": "", "kind": UserKind.SERVICE}

    def update_service_identity(self, *, username: str, name: str) -> None:
        """Persist only changed service identity facts through native User.save."""

        values = self.service_identity_values(username=username, name=name)
        changed = {field for field, value in values.items() if getattr(self, field) != value}
        for field in changed:
            setattr(self, field, values[field])
        if changed:
            self.save(using=self._state.db, update_fields=changed)

    def deactivate_service(self) -> None:
        """Retain a deleted service owner's identity while preventing further use."""

        if self.kind != UserKind.SERVICE:
            raise ValidationError("Only service accounts can be deactivated by a service owner.")
        if self.is_active:
            self.is_active = False
            self.save(using=self._state.db, update_fields={"is_active"})

    def update_preferences(self, preferences: Mapping[str, Any]) -> None:
        """Replace this user's private UI preference object."""

        if not isinstance(preferences, Mapping):
            raise ValueError("preferences must be a JSON object")

        with system_context(reason="iam.preferences.update"), transaction.atomic():
            self.preferences = dict(preferences)
            self.save(update_fields=["preferences"])

    def __str__(self) -> str:
        """Use IAM's human label for person and service-user references alike."""

        return user_label(self, guard=False)

    def get_full_name(self) -> str:
        """Return first and last name joined with a space."""

        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        """Return the user's short display name."""

        return self.first_name


class ServiceUserMixin(PublicIdentity, models.Model):
    """A saved row's stable non-login user relation and synchronization.

    Participants declare a stable ``service_user_prefix``. The default native
    relation is nullable, protected and named after the participant; a native
    abstract-field override retains distinct reverse names or editability.
    Its public id supplies the stable key and its label supplies the user name.
    """

    service_user_prefix: ClassVar[str]
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="%(class)s",
    )
    """Non-login user authenticating, authorizing and attributing this row's actions."""

    class Meta:
        abstract = True

    def sync_service_user(self) -> User:
        """Synchronize the linked principal after this row's own write admission."""

        if self.pk is None:
            raise ValueError("The service owner must be saved before syncing its user.")
        owner_rows = type(self)._base_manager.db_manager(self._state.db, hints={"instance": self})
        using = owner_rows.db
        with database_context(using), system_context(reason="iam.service_user.link"), transaction.atomic(using=using):
            owner = owner_rows.select_for_update().get(pk=self.pk)
            user = (
                get_user_model()
                .objects.db_manager(using)
                .ensure_service_user(
                    username=f"{self.service_user_prefix}-{self.public_id}",
                    name=str(owner),
                    user_id=owner.user_id,
                )
            )
            if owner.user_id != user.pk:
                owner_rows.filter(pk=self.pk).update(user_id=user.pk)
            self.user = user
            return user

    def deactivate_service_user(self) -> None:
        """Retain attribution when native deletion removes this service owner."""

        if self.user_id is None:
            return
        using = type(self)._base_manager.db_manager(self._state.db, hints={"instance": self}).db
        with database_context(using), system_context(reason="iam.service_user.deactivate"):
            user = get_user_model().objects.using(using).get(pk=self.user_id)
            user.deactivate_service()
