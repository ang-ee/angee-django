"""Django config for the web forms and messaging bridge."""

from __future__ import annotations

from django.apps import AppConfig, apps
from django.core import checks
from django.db.models.signals import pre_migrate


class WebformsMessagingConfig(AppConfig):
    """Source app manifest for the message target, the webform channel backend and their upgrade."""

    default = True
    name = "angee.webforms_messaging"

    def ready(self) -> None:
        """Keep a channel's form columns until the move has carried the forms.

        The receiver is connected for messaging's app config, so it acts once
        per ``migrate`` run: Django sends ``pre_migrate`` once for every app
        that has a models module, and the table and the migrations it guards
        are messaging's, which always has one.
        """

        super().ready()
        # Both read the migration template, which needs populated apps.
        from angee.webforms_messaging.checks import check_form_columns_leave_with_the_move
        from angee.webforms_messaging.signals import refuse_removal_ahead_of_the_move

        pre_migrate.connect(
            refuse_removal_ahead_of_the_move,
            sender=apps.get_app_config("messaging"),
            weak=False,
            dispatch_uid="angee.webforms_messaging.form_columns",
        )
        checks.register(check_form_columns_leave_with_the_move, checks.Tags.database)
