"""Register kind source models with the shared model-change seam."""

from functools import partial

from django.apps import AppConfig
from django.core import checks

from angee.base.hooks import check_hooks

check_settings = partial(check_hooks, ("ANGEE_TIMELINE_KINDS",))


class TimelineConfig(AppConfig):
    default = True
    name = "angee.timeline"

    def ready(self) -> None:
        from django.db.models.signals import pre_delete, pre_save

        from angee.base.models.base import AngeeModel
        from angee.base.signals import bulk_updated, change_published, connect_for_models, connect_observers
        from angee.timeline.events import before_delete, before_save, kinds_for, queue_change, queue_updates

        checks.register(check_settings, checks.Tags.models)

        connect_for_models(
            change_published, queue_change, applies=lambda model: bool(kinds_for(model)),
            prepare=lambda model: connect_observers(model)
            if any(not kind.explicit_publication for kind in kinds_for(model)) else None,
            dispatch_uid="timeline.invalidate",
        )
        for signal, receiver in ((pre_save, before_save), (pre_delete, before_delete)):
            connect_for_models(
                signal, receiver,
                applies=lambda model: any(not kind.explicit_publication for kind in kinds_for(model)),
                dispatch_uid=f"timeline.before.{signal is pre_save}",
            )
        connect_for_models(
            bulk_updated, queue_updates,
            applies=lambda model: bool(kinds_for(model))
            and issubclass(model, AngeeModel) and model.change_batch_targets is not None,
            dispatch_uid="timeline.batch",
        )
