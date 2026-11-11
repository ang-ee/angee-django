"""Messaging composes the canonical model-label field contract."""

from rebac import system_context

from angee.base.fields import ModelLabelField
from angee.messaging.managers import message_subtype_options
from angee.messaging.testing.models import MessageSubtype


def test_subtype_scope_boundary_cleanup_precedes_field_canonicalization(db: None) -> None:
    del db
    with system_context(reason="messaging field owner regression"):
        subtype = MessageSubtype.objects.resolve("field-contract", model_label=" messaging.thre\x00ad ")
        assert subtype.model_label == "messaging.Thread"
        assert any(option["key"] == "field-contract" for option in message_subtype_options(" messaging.thre\x00ad "))
        assert isinstance(MessageSubtype._meta.get_field("model_label"), ModelLabelField)


def test_subtype_resolve_creates_once_and_keeps_the_first_declared_labels(db: None) -> None:
    """Built-in keys take their shipped labels; other keys their supplied or humanized ones, once."""

    del db
    with system_context(reason="messaging subtype resolve"):
        assert MessageSubtype.objects.resolve("  ") is None
        comment = MessageSubtype.objects.resolve("comment", name="Ignored")
        assert (comment.name, comment.description) == ("Comment", "Discussion comment")
        humanized = MessageSubtype.objects.resolve("stage_moved")
        assert (humanized.name, humanized.description) == ("Stage moved", "Stage moved")
        declared = MessageSubtype.objects.resolve(
            "source_note",
            model_label="messaging.Thread",
            name="Source note",
            description="From the source",
            internal=True,
        )
        again = MessageSubtype.objects.resolve("source_note", model_label="messaging.Thread", name="Renamed")
        assert again.pk == declared.pk
        assert (again.name, again.description, again.internal) == ("Source note", "From the source", True)
        assert MessageSubtype.objects.resolve("source_note").pk != declared.pk
