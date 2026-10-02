"""Per-model receiver binding."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.apps import apps
from django.db.models import Model
from django.db.models.signals import ModelSignal, class_prepared


def connect_for_models(
    signal: ModelSignal,
    receiver: Callable[..., Any],
    *,
    applies: Callable[[type[Model]], bool],
    dispatch_uid: str,
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
        signal.connect(receiver, sender=model, dispatch_uid=f"{dispatch_uid}.{model._meta.label_lower}")

    for model in apps.get_models():
        bind(model)
    # Models prepared after app population (test-defined records, for one) bind as they finalize.
    class_prepared.connect(
        lambda sender, **_kwargs: bind(sender), dispatch_uid=f"{dispatch_uid}.class_prepared", weak=False,
    )
