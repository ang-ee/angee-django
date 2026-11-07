"""Per-model receiver binding and the post-migrate receiver guard."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from django.apps import apps
from django.db import connections, router
from django.db.models import Model
from django.db.models.signals import class_prepared, post_save
from django.dispatch import Signal

pre_merge = Signal()
"""Enter addon-owned contexts around one record fold (survivor, join, contexts).

``contexts`` is the fold's ExitStack. Receivers may enter a context there;
its cleanup runs after the fold, including when validation or a write fails.
"""

bulk_updated = Signal()
"""An owning bulk verb changed a batch of rows (instances, update_fields, using).

Observers receive persisted rows without pretending they were individually saved:
the queryset already owns history and authorization for the write.
"""

record_access_revoked = Signal()
"""A direct record relation was revoked (instance, relation, subject, using).

The subject is the normalized native SubjectRef. Receivers run synchronously
inside the relationship write transaction, including for an absent tuple.
"""


def send_created_batch(
    *, sender: type[Model], instances: Iterable[Model], using: str, **kwargs: Any,
) -> None:
    """Send every native creation event, carrying the immutable persisted batch.

    Batch-aware observers act on its final send; all ordinary receivers still
    receive each instance. Callers finish initialization and actor binding first.
    """

    rows = tuple(instances)
    for instance in rows:
        post_save.send(
            sender=sender, instance=instance, created=True, raw=False, using=using,
            update_fields=None, created_batch=rows, **kwargs,
        )


def migrated_model(*, app_config: object, using: str, app_label: str, model_name: str) -> type[Model] | None:
    """Return the model a ``post_migrate`` receiver may work on now, or ``None``.

    Django sends ``post_migrate`` for every app on every migrate, test databases
    included, so a receiver acts once: on its own app's signal, for the connection
    the router writes the model to, and only while migrations have created the
    model's table (not after migrating back past it).
    """

    if getattr(app_config, "label", "") != app_label:
        return None
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return None
    if using != router.db_for_write(model) or not router.allow_migrate_model(using, model):
        return None
    return model if has_table(using, model) else None


def has_table(using: str, model: type[Model]) -> bool:
    """Return whether migrations have created ``model``'s table on the ``using`` connection."""

    connection = connections[using]
    with connection.cursor() as cursor:
        return model._meta.db_table in connection.introspection.table_names(cursor)


def connect_for_models(
    signal: Signal,
    receiver: Callable[..., Any],
    *,
    applies: Callable[[type[Model]], bool],
    dispatch_uid: str,
    prepare: Callable[[type[Model]], None] | None = None,
) -> None:
    """Connect ``receiver`` to each concrete model ``applies`` admits, now and as models prepare.

    A model signal receiver connected without a sender listens to every model, and a
    delete receiver of that kind stops Django from deleting any table's rows in bulk:
    the collector loads each row to send it. Owners bind to the models they concern
    instead. Proxies bind too, since a delete through a proxy queryset sends the proxy.
    Historical migration models never bind.
    """

    def bind(model: type[Model]) -> None:
        if model._meta.abstract or model._meta.apps is not apps or not applies(model):
            return
        if prepare is not None:
            prepare(model)
        signal.connect(receiver, sender=model, dispatch_uid=f"{dispatch_uid}.{model._meta.label_lower}")

    for model in apps.get_models():
        bind(model)
    # Models prepared after app population (test-defined records, for one) bind as they finalize.
    class_prepared.connect(
        lambda sender, **_kwargs: bind(sender), dispatch_uid=f"{dispatch_uid}.class_prepared", weak=False,
    )
