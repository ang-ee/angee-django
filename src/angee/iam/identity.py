"""IAM subject identity and public user labels.

Group namespaces come from the installed model declaration. User labels and
public IDs use Django's auth model contract without exposing guarded user rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models import CharField, OuterRef, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce, Concat, NullIf, Trim
from django.http import HttpRequest
from rebac import SubjectRef, system_context
from rebac.resources import model_for_resource_type, model_resource_type

from angee.base.identity import public_data_id_field
from angee.base.models.identity import instance_from_public_id, instances_from_public_ids, public_id_for
from angee.base.models.persistence import require_projection


def group_subject_type() -> str:
    """Read the group subject namespace from its owning installed Django model."""

    resource_type = model_resource_type(apps.get_model("iam", "Group"))
    if resource_type is None:
        raise ImproperlyConfigured("IAM Group must declare its REBAC resource identity.")
    return resource_type


def user_label(user: Any, *, guard: bool = True) -> str:
    """Return any user model's human label from the Django auth contract.

    A dedicated label queryset projects the display scalar before field
    redaction. An ordinary actor-scoped User row still respects redaction.
    """

    if "_iam_display_label" in user.__dict__:
        return str(user.__dict__["_iam_display_label"] or "")
    if guard and user.get_deferred_fields() & {"first_name", "last_name", "username"}:
        require_projection(user, "_iam_display_label")
    return str(user.get_full_name() or user.username or "")


def user_label_queryset() -> QuerySet[Any]:
    """Read only the display label disclosed by an already-readable record.

    Audit references expose a scalar label, not a User row. The unscoped base
    manager avoids a REBAC bypass event for this narrow projection; callers
    must return only :func:`user_label`'s result, never the source instance.
    A nameless account's label is its sign-in name, even though username is a
    read_private field; this scalar disclosure follows every readable record.
    """

    return get_user_model()._base_manager.only(
        "first_name", "last_name", "username",
    ).annotate(_iam_display_label=user_label_expression())


def user_label_expression(prefix: str = "") -> Coalesce:
    """Project the auth user's display label in SQL, including through a relation."""

    full_name = Trim(Concat(f"{prefix}first_name", Value(" "), f"{prefix}last_name"))
    return Coalesce(NullIf(full_name, Value("")), f"{prefix}username", output_field=CharField())


def user_label_annotation(relation: str) -> Subquery:
    """Project one linked account's public display label as a scalar only."""

    return Subquery(
        user_label_queryset().filter(pk=OuterRef(f"{relation}_id"))
        .order_by().values("_iam_display_label")[:1]
    )


def user_public_id(user_id: Any) -> str | None:
    """Return a user's opaque public id without fetching the user row."""

    if user_id is None:
        return None
    return public_id_for(get_user_model(), user_id)


def _label_memo(request: HttpRequest | None) -> dict[Any, str | None] | None:
    """Return the per-request user-label memo, creating it on first use.

    Hung on the Django request — the per-request seam IAM already uses for
    request-scoped state — so a list of rows sharing an author resolves the
    label (and queries the user) once instead of per row. Absent outside a
    Django HTTP request, where resolution falls back to the uncached path.
    """

    if not isinstance(request, HttpRequest):
        return None
    memo: dict[Any, str | None] | None = getattr(request, "_iam_user_label_memo", None)
    if memo is None:
        memo = {}
        setattr(request, "_iam_user_label_memo", memo)
    return memo


def user_display_labels(
    user_ids: Iterable[Any],
    *,
    request: HttpRequest | None = None,
) -> dict[Any, str | None]:
    """Return user display labels for ``user_ids`` with one narrow read.

    The label is the disclosure carried by an already-readable record or
    principal. Only the display string leaves this helper; User rows remain
    inside IAM and the queryset selects no credential fields.

    Pass the Django ``request`` to prime and reuse the request-scoped label memo:
    list resolvers can ask for all distinct ids up front and pay one query.
    """

    ids = [user_id for user_id in dict.fromkeys(user_ids) if user_id is not None]
    if not ids:
        return {}
    memo = _label_memo(request)
    labels: dict[Any, str | None] = {}
    missing: list[Any] = []
    for user_id in ids:
        if memo is not None and user_id in memo:
            labels[user_id] = memo[user_id]
        else:
            missing.append(user_id)
    if not missing:
        return labels

    user_model = get_user_model()
    # Role grants carry canonical primary keys; public input carries model-owned
    # identifiers. Turn only canonical keys into public ids through that owner,
    # then let its batch resolver decode and retrieve the whole set once.
    pk = user_model._meta.pk
    public_ids: dict[str, str] = {}
    for user_id in missing:
        public_id = str(user_id)
        if pk is not None:
            try:
                canonical = pk.to_python(user_id)
            except TypeError, ValueError, ValidationError:
                pass
            else:
                public_id = public_id_for(user_model, canonical)
        public_ids[str(user_id)] = public_id
    users = instances_from_public_ids(user_model, public_ids.values(), queryset=user_label_queryset())
    fetched = {key: user_label(users[value]) for key, value in public_ids.items() if value in users}

    for user_id in missing:
        fetched_label = fetched.get(str(user_id))
        labels[user_id] = fetched_label
        if memo is not None:
            memo[user_id] = fetched_label
    return labels


def user_display_label(user_id: Any, *, request: HttpRequest | None = None) -> str | None:
    """Return one user's display label without exposing the user object."""

    if user_id is None:
        return None
    memo = _label_memo(request)
    if memo is not None and user_id in memo:
        return memo[user_id]
    label = user_display_labels((user_id,), request=request).get(user_id)
    if label is not None:
        return label
    try:
        user = user_principal(str(user_id))
    except ValueError:
        return None
    label = user_label(user)
    if memo is not None:
        memo[user_id] = label
    return label


def user_principal(principal_id: str) -> Any:
    """Return the user addressed by a role-grant principal id."""

    user_model = get_user_model()
    with system_context(reason="iam.identity.principal"):
        try:
            canonical = user_model._meta.pk.to_python(principal_id)
        except TypeError, ValueError, ValidationError:
            canonical = None
        if canonical is not None:
            user = user_model._default_manager.filter(pk=canonical).first()
            if user is not None:
                return user
        user = instance_from_public_id(user_model, principal_id)
        if user is not None:
            return user
    raise ValueError(f"User principal {principal_id!r} was not found.")


def user_from_public_id(user_id: Any) -> Any:
    """Return the user addressed by one GraphQL public id, or raise."""

    user_model = get_user_model()
    with system_context(reason="iam.identity.user.lookup"):
        user = instance_from_public_id(user_model, str(user_id))
    if user is None:
        raise ValueError(f"User {user_id!s} was not found.")
    return user


def canonical_subject_ref(value: str) -> SubjectRef:
    """Decode a transport subject into its canonical REBAC identity."""

    subject = SubjectRef.parse(value)
    if subject.subject_id in {"", "*"}:
        return subject
    model = model_for_resource_type(subject.subject_type)
    if model is None or not model._meta.managed:
        return subject
    field = public_data_id_field(model)
    if field is None:
        return subject
    pk = field.public_id_to_value(subject.subject_id)
    if pk is None:
        if not field.prefix:
            raise ValueError(f"Subject {value!r} has an invalid public id.")
        model_pk = model._meta.pk
        if model_pk is None:
            raise ValueError(f"Subject {value!r} has no model identity.")
        try:
            pk = model_pk.to_python(subject.subject_id)
        except (TypeError, ValueError, ValidationError) as error:
            raise ValueError(f"Subject {value!r} has an invalid canonical id.") from error
    return SubjectRef.of(subject.subject_type, str(pk), subject.optional_relation)

def public_subject_ref(subject: SubjectRef) -> SubjectRef:
    """Encode a canonical model-backed subject for a transport boundary."""

    if subject.subject_id in {"", "*"}:
        return subject
    model = model_for_resource_type(subject.subject_type)
    if model is None or not model._meta.managed:
        return subject
    field = public_data_id_field(model)
    if field is None:
        return subject
    return SubjectRef.of(
        subject.subject_type,
        field.public_id_from_value(subject.subject_id),
        subject.optional_relation,
    )
