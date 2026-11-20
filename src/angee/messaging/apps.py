"""Django config for Angee's messaging addon."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks
from django.db.models.signals import pre_migrate


class MessagingConfig(AppConfig):
    """Source app manifest for threads, messages, and channel bridges."""

    default = True
    name = "angee.messaging"
    requires_apps = ('django.contrib.contenttypes', 'django.contrib.postgres')

    def ready(self) -> None:
        from angee.base.signals import connect_for_models, record_access_revoked
        from angee.messaging.checks import check_derived_reads
        from angee.messaging.events import end_unreadable_follows
        from angee.messaging.models import ThreadedModelMixin
        from angee.messaging.webform_columns import (
            check_form_columns_leave_with_the_move,
            refuse_removal_ahead_of_the_move,
        )

        checks.register(check_derived_reads, checks.Tags.security)
        # Interim, with the module: a channel's form columns do not leave while they hold a form. Django sends
        # pre_migrate once for each app; connected for this one, the receiver acts once per run.
        checks.register(check_form_columns_leave_with_the_move, checks.Tags.database)
        pre_migrate.connect(
            refuse_removal_ahead_of_the_move,
            sender=self,
            weak=False,
            dispatch_uid="angee.messaging.webform_columns",
        )

        connect_for_models(
            record_access_revoked,
            end_unreadable_follows,
            applies=lambda model: issubclass(model, ThreadedModelMixin),
            dispatch_uid="messaging.record_access_revoked",
        )
