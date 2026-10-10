"""Watch-only save observations, independent of workflow admission sources."""

from typing import Any, ClassVar

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models.signals import post_save

from angee.base.impl import ImplBase, resolve_all_impl_classes
from angee.workflows.triggers import TriggerSource


class RecordWatch(ImplBase):
    """Declare a model and fields whose saves wake existing step watches.

    Registration confers no trigger scope or record access. StepWatch owns the
    transaction and actor-readable observation; this adapter only selects saves.
    """

    registry_setting = "ANGEE_WORKFLOW_WATCH_CLASSES"
    model_label: ClassVar[str] = ""
    fields: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def model(cls) -> Any:
        model = apps.get_model(cls.model_label)
        if model._meta.app_label in {"workflows", "decisions"}:
            raise ImproperlyConfigured("Workflow and decision rows use their native await owners.")
        for name in cls.fields:
            model._meta.get_field(name)
        return model

    @classmethod
    def connect_registered(cls) -> None:
        """Connect deterministic declarations, refusing competing model observers."""
        seen = set()
        for implementation in resolve_all_impl_classes(cls):
            model = implementation.model()
            if model in seen:
                raise ImproperlyConfigured(f"Multiple watch registrations for {model._meta.label}.")
            for source in TriggerSource.registered():
                try:
                    source.validate_model(model)
                except ValidationError:
                    continue
                raise ImproperlyConfigured(f"{model._meta.label} already has a native trigger observation.")
            seen.add(model)
            post_save.connect(
                implementation.changed, sender=model, weak=False,
                dispatch_uid=f"workflows.watch.{model._meta.label_lower}",
            )

    @classmethod
    def check_model(cls, model: Any) -> None:
        """Allow an explicit watch declaration or an existing native trigger source."""
        if any(implementation.model() is model for implementation in resolve_all_impl_classes(cls)):
            return
        for source in TriggerSource.registered():
            try:
                source.validate_model(model)
            except ValidationError:
                continue
            return
        raise ValidationError("This model has no registered workflow observation.")

    @classmethod
    def changed(
        cls, sender: Any, *, instance: Any, raw: bool = False,
        update_fields: frozenset[str] | None = None, **kwargs: Any,
    ) -> None:
        """Ignore fixture loads and saves that cannot change the watched predicate."""
        if not raw and (update_fields is None or not cls.fields or set(cls.fields).intersection(update_fields)):
            apps.get_model("workflows", "StepWatch").objects.record_change(instance)
