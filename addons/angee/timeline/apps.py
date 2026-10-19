"""Django identity for the record timeline: the ledger follows its kinds' models."""

from __future__ import annotations

from typing import Any

from django.apps import AppConfig
from django.db.models.signals import m2m_changed, post_save

M2M_ACTIONS = ("post_add", "post_remove", "post_clear")


class TimelineConfig(AppConfig):
    default = True
    name = "angee.timeline"

    def ready(self) -> None:
        # Kinds name their models by label, and composed test projects register
        # those models after app population: match senders at dispatch, not here.
        from angee.timeline.events import kinds_for, sync_events

        def resync(sender: Any, instance: Any, **kwargs: Any) -> None:
            if kwargs.get("raw") or kwargs.get("reverse"):
                return
            action = kwargs.get("action")
            if action is not None and action not in M2M_ACTIONS:
                return
            kinds = kinds_for(type(instance))
            if kinds:
                sync_events(instance, kinds=kinds)

        post_save.connect(resync, weak=False, dispatch_uid="timeline.sync")
        m2m_changed.connect(resync, weak=False, dispatch_uid="timeline.sync.m2m")
