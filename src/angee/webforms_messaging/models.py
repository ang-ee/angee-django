"""The rule the bridge contributes to ``webforms.Form``: one form per conversation."""

from __future__ import annotations

from collections.abc import Collection

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.fields.json import KeyTextTransform
from django_compose.models import Composable

TAKEN = "Another form already posts on this channel under this slug."


class FormConversation(Composable, extends="webforms.Form"):
    """One form per channel and slug among the forms whose target is ``message``.

    A response's message is ``webform:<slug>:<submission id>`` on its channel.
    Two forms that named one channel and slug would share those ids, so a
    submission to one could replace the message of the other. ``clean`` refuses
    the second form with a field error, and the constraint refuses it when two
    are saved at once.
    """

    class Meta:
        """Abstract contribution folded into the concrete form table."""

        abstract = True
        constraints = (
            models.UniqueConstraint(
                KeyTextTransform("channel", "target_config"),
                KeyTextTransform("slug", "target_config"),
                condition=models.Q(target_class="message"),
                name="%(app_label)s_form_message_conversation",
                violation_error_message=TAKEN,
            ),
        )

    def clean(self) -> None:
        """Refuse a ``message`` target whose channel and slug another form already names."""

        super().clean()
        if str(self.target_class) != "message":
            return
        config = self.target_config
        others = type(self)._base_manager.filter(
            target_class="message",
            target_config__channel=config.get("channel"),
            target_config__slug=config.get("slug"),
        ).exclude(pk=self.pk)
        if others.exists():
            raise ValidationError({"target_config": TAKEN})

    def validate_constraints(self, exclude: Collection[str] | None = None) -> None:
        """Leave the conversation's uniqueness to ``clean``, which reads every form whoever asks.

        Django would check the constraint through the default manager, which
        reads only what the acting user may read and needs one to read at all.
        """

        super().validate_constraints({*(exclude or ()), "target_config"})
