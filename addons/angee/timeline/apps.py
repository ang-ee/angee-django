"""Register kind source models with the shared model-change seam."""

from django.apps import AppConfig


class TimelineConfig(AppConfig):
    default = True
    name = "angee.timeline"

    def ready(self) -> None:
        from angee.graphql.publishing import change_published, connect_observers
        from angee.signals import bulk_updated, connect_for_models
        from angee.timeline.events import kinds_for, queue_change, queue_updates

        connect_for_models(
            change_published, queue_change, applies=lambda model: bool(kinds_for(model)),
            prepare=lambda model: connect_observers(model)
            if any(not kind.explicit_publication for kind in kinds_for(model)) else None,
            dispatch_uid="timeline.sync",
        )
        connect_for_models(
            bulk_updated, queue_updates,
            applies=lambda model: bool(kinds_for(model)) and callable(getattr(model, "change_batch_targets", None)),
            dispatch_uid="timeline.batch",
        )
