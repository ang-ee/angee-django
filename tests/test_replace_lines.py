"""Tests for ``AngeeModel.replace_lines``: a parent owns its lines and writes them natively."""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from tests.conftest import create_user
from tests.linesdemo.models import Document, DocumentLine, PinnedLine, Product, Tag


def _grant(document: Document, relation: str, user: Any) -> None:
    write_relationships([RelationshipTuple(to_object_ref(document), relation, to_subject_ref(user))])


def _rows(document: Document) -> list[tuple[str, int, int]]:
    with system_context(reason="test.replace_lines.read"):
        return list(document.lines.order_by("position").values_list("label", "quantity", "position"))


def test_replacing_lines_checks_the_parent_write_before_elevating(composed_tables: None) -> None:
    """A reader of the parent is refused before any line is written, even with an elevation available."""

    reader = create_user("lines-reader")
    with system_context(reason="test.replace_lines.seed"):
        document = Document.objects.create(title="Document")
        line = DocumentLine.objects.create(document=document, label="Line", quantity=1, position=0)
    _grant(document, "reader", reader)
    loaded = Document.objects.with_actor(reader).get(pk=document.pk)

    with pytest.raises(PermissionDenied):
        loaded.replace_lines("lines", [(line.public_id, {"label": "Tampered"}), (None, {"label": "Injected"})])

    assert _rows(document) == [("Line", 1, 0)]


def test_replace_lines_writes_native_values(composed_tables: None) -> None:
    """Updates, creations and removals take foreign keys by attname and many-to-many sets."""

    writer = create_user("lines-writer")
    with system_context(reason="test.replace_lines.native_seed"):
        document = Document.objects.create(title="Document")
        keep = DocumentLine.objects.create(document=document, label="Keep", quantity=1, position=0)
        DocumentLine.objects.create(document=document, label="Drop", quantity=2, position=1)
        product = Product.objects.create(name="Widget")
        tags = [Tag.objects.create(name=name) for name in ("red", "blue")]
    _grant(document, "manager", writer)
    loaded = Document.objects.with_actor(writer).get(pk=document.pk)

    loaded.replace_lines("lines", [
        (keep.public_id, {"label": "Kept", "quantity": 3, "position": 0, "tags": tags}),
        (None, {"label": "New", "quantity": 5, "position": 1, "product_id": product.pk, "tags": [tags[0].pk]}),
    ])

    assert _rows(document) == [("Kept", 3, 0), ("New", 5, 1)]
    with system_context(reason="test.replace_lines.native_read"):
        created = DocumentLine.objects.get(document=document, label="New")
        assert created.product_id == product.pk
        assert set(created.tags.values_list("pk", flat=True)) == {tags[0].pk}
        assert set(DocumentLine.objects.get(pk=keep.pk).tags.values_list("pk", flat=True)) == {t.pk for t in tags}


def test_appending_lines_for_a_new_parent_keeps_provisioned_lines(composed_tables: None) -> None:
    """``replace=False`` serves the creation that authorized the parent: it only adds."""

    with system_context(reason="test.replace_lines.append_seed"):
        document = Document.objects.create(title="Document")
        DocumentLine.objects.create(document=document, label="Provisioned", quantity=1, position=0)
        document.replace_lines("lines", [(None, {"label": "Added", "quantity": 2, "position": 1})], replace=False)

    assert _rows(document) == [("Provisioned", 1, 0), ("Added", 2, 1)]


def test_replace_lines_meets_the_line_row_lock_under_elevation(composed_tables: None) -> None:
    """The elevation authorizes the write; a locked line's own guard still refuses it."""

    writer = create_user("lines-locked-writer")
    with system_context(reason="test.replace_lines.lock_seed"):
        document = Document.objects.create(title="Document")
        pinned = PinnedLine.objects.create(document=document, label="Pinned", pinned=True, position=0)
    _grant(document, "manager", writer)
    loaded = Document.objects.with_actor(writer).get(pk=document.pk)

    with pytest.raises(ValidationError, match="cannot change"):
        loaded.replace_lines("pinned_lines", [(pinned.public_id, {"label": "Renamed", "position": 0})])
    with pytest.raises(ValidationError, match="cannot be deleted"):
        loaded.replace_lines("pinned_lines", [])
    with system_context(reason="test.replace_lines.lock_read"):
        assert PinnedLine.objects.get(pk=pinned.pk).label == "Pinned"
