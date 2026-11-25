"""Canonical concrete message template model for the bare-Django test runtime.

One concrete model per resource type; the composed runtime emits its own.
"""

from angee.messaging_templates.models import MessageTemplate as AbstractMessageTemplate


class MessageTemplate(AbstractMessageTemplate):
    """Concrete message template used by the message template tests."""

    class Meta(AbstractMessageTemplate.Meta):
        """Django model options for the canonical test message template."""

        abstract = False
        app_label = "messaging_templates"
        db_table = "test_messaging_templates_message_template"
        rebac_resource_type = "messaging_templates/message_template"
