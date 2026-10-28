"""Backend SQL bridges retain Django's correlation, JSON values, and parameters."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator

import pytest
from django.db import models
from django.db.models.functions import Cast, JSONObject
from django.test.utils import isolate_apps

from angee.base.models.expressions import JSONArraySubquery, TextSHA256
from tests.tables import model_tables


@pytest.fixture
def expression_models(transactional_db: None) -> Iterator[tuple[type[models.Model], ...]]:
    del transactional_db
    with isolate_apps():
        class Container(models.Model):
            title = models.CharField(max_length=80)

            class Meta:
                app_label = "expression_contract"

        class Entry(models.Model):
            container = models.ForeignKey(Container, on_delete=models.CASCADE)
            position = models.IntegerField()
            title = models.CharField(max_length=80)

            class Meta:
                app_label = "expression_contract"

        class Detail(models.Model):
            entry = models.ForeignKey(Entry, on_delete=models.CASCADE)
            title = models.CharField(max_length=80)

            class Meta:
                app_label = "expression_contract"

        with model_tables((Container, Entry, Detail)):
            yield Container, Entry, Detail


def test_json_array_subquery_preserves_nested_values_order_and_correlation(expression_models) -> None:
    Container, Entry, Detail = expression_models
    first = Container._default_manager.create(title="First")
    second = Container._default_manager.create(title="Second")
    empty = Container._default_manager.create(title="Empty")
    one = Entry._default_manager.create(container=first, position=1, title="O'Reilly ☃")
    Entry._default_manager.create(container=first, position=2, title="Newest")
    other = Entry._default_manager.create(container=second, position=1, title="Other")
    Detail._default_manager.create(entry=one, title="z")
    Detail._default_manager.create(entry=one, title="a")
    Detail._default_manager.create(entry=other, title="separate")
    details = Detail._default_manager.filter(entry_id=models.OuterRef("pk")).order_by("title").values(
        _item=JSONObject(title=models.F("title")),
    )
    entries = Entry._default_manager.filter(container_id=models.OuterRef("pk")).order_by("-position").values(
        _item=JSONObject(title=models.F("title"), details=JSONArraySubquery(details)),
    )
    rows = dict(Container._default_manager.annotate(items=JSONArraySubquery(entries)).values_list("pk", "items"))
    assert rows[first.pk] == [
        {"title": "Newest", "details": []},
        {"title": "O'Reilly ☃", "details": [{"title": "a"}, {"title": "z"}]},
    ]
    assert rows[second.pk] == [{"title": "Other", "details": [{"title": "separate"}]}]
    assert rows[empty.pk] == []


@pytest.mark.parametrize("value", ("", "O'Reilly ☃", None))
def test_text_sha256_hashes_the_native_text_and_preserves_null(expression_models, value) -> None:
    Container, _Entry, _Detail = expression_models
    Container._default_manager.create(title="row")
    expression = JSONObject(title=models.Value(value, output_field=models.TextField()))
    row = Container._default_manager.annotate(
        rendered=Cast(expression, output_field=models.TextField()),
        digest=TextSHA256(Cast(expression, output_field=models.TextField())),
        direct_digest=TextSHA256(models.Value(value, output_field=models.TextField())),
    ).get()
    assert row.digest == hashlib.sha256(row.rendered.encode("utf8")).hexdigest()
    expected = hashlib.sha256(value.encode("utf8")).hexdigest() if value is not None else None
    assert row.direct_digest == expected
