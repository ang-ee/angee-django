"""Stock-Django GenericRelation compiles content types on the query database."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_generic_relation_database_contract() -> None:
    # Adapt the four GenericRelationDatabaseTests from Django fork fd0ed9ac3b
    # to one isolated dual-database host using Angee's relation subclass.
    script = r'''
from django.apps import AppConfig
from django.conf import settings

class RelationProbeConfig(AppConfig):
    name = 'tests'
    label = 'relation_probe'

class DefaultRouter:
    def db_for_read(self, model, **hints):
        return 'default'
    db_for_write = db_for_read

settings.configure(
    INSTALLED_APPS=['django.contrib.contenttypes', '__main__.RelationProbeConfig'],
    SECRET_KEY='generic-relation-database',
    DATABASE_ROUTERS=[DefaultRouter()],
    DATABASES={alias: {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}
               for alias in ('default', 'selected')},
)
import django
django.setup()

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import connections, models
from django.db.models import Count
from django.test.utils import CaptureQueriesContext
from angee.base.fields.relations import GenericRelation

class TaggedItem(models.Model):
    tag = models.SlugField()
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey()

    class Meta:
        app_label = 'relation_probe'

class Animal(models.Model):
    common_name = models.CharField(max_length=150)
    latin_name = models.CharField(max_length=150)
    tags = GenericRelation(TaggedItem, related_query_name='animal')

    class Meta:
        app_label = 'relation_probe'

class AnimalProxy(Animal):
    class Meta:
        app_label = 'relation_probe'
        proxy = True

class ForProxyModelModel(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    obj = GenericForeignKey(for_concrete_model=False)
    title = models.CharField(max_length=255, null=True)

    class Meta:
        app_label = 'relation_probe'

class ConcreteRelatedModel(models.Model):
    bases = GenericRelation(ForProxyModelModel, for_concrete_model=False)

    class Meta:
        app_label = 'relation_probe'

class ProxyRelatedModel(ConcreteRelatedModel):
    class Meta:
        app_label = 'relation_probe'
        proxy = True

for alias in ('default', 'selected'):
    with connections[alias].schema_editor() as editor:
        for model in (ContentType, Animal, TaggedItem, ConcreteRelatedModel, ForProxyModelModel):
            editor.create_model(model)

# Each database allocates its own content-type identities.
ContentType.objects.using('selected').create(app_label='relation_probe', model='other')
for alias in ('default', 'selected'):
    for pk in (11, 12):
        Animal.objects.using(alias).create(
            pk=pk, common_name=f'{alias}-{pk}', latin_name='example',
        )
    animal_pk = 11 if alias == 'default' else 12
    TaggedItem.objects.using(alias).create(
        tag='wanted',
        content_type_id=ContentType.objects.db_manager(alias).get_for_model(Animal).pk,
        object_id=animal_pk,
    )
    ConcreteRelatedModel.objects.using(alias).create(pk=21)
    ProxyRelatedModel.objects.using(alias).create(pk=22)
    for model, pk in ((ConcreteRelatedModel, 21), (ProxyRelatedModel, 22)):
        ForProxyModelModel.objects.using(alias).create(
            content_type_id=ContentType.objects.db_manager(alias).get_for_model(
                model, for_concrete_model=False,
            ).pk,
            object_id=pk,
            title='wanted',
        )
default_type = ContentType.objects.db_manager('default').get_for_model(Animal).pk
selected_type = ContentType.objects.db_manager('selected').get_for_model(Animal).pk
assert default_type != selected_type

def test_join_query_reuse():
    ContentType.objects.clear_cache()
    with CaptureQueriesContext(connections['default']) as default_queries:
        query = Animal.objects.filter(tags__tag='wanted').order_by('pk')
        assert list(query.using('selected').values_list('pk', flat=True)) == [12]
    assert len(default_queries) == 0
    with CaptureQueriesContext(connections['selected']) as selected_queries:
        assert list(query.using('default').values_list('pk', flat=True)) == [11]
    assert len(selected_queries) == 0
    with CaptureQueriesContext(connections['default']) as default_queries:
        assert list(query.using('selected').values_list('pk', flat=True)) == [12]
    assert len(default_queries) == 0

def test_join_relabeling_and_aggregation():
    ContentType.objects.clear_cache()
    with CaptureQueriesContext(connections['default']) as default_queries:
        nested = Animal.objects.filter(tags__tag='wanted').values('pk')
        assert list(
            Animal.objects.using('selected').filter(pk__in=nested).values_list('pk', flat=True)
        ) == [12]
        assert list(
            Animal.objects.using('selected').order_by('pk')
            .annotate(total=Count('tags')).values_list('pk', 'total')
        ) == [(11, 0), (12, 1)]
        assert list(
            TaggedItem.objects.using('selected')
            .filter(animal__common_name='selected-12').values_list('object_id', flat=True)
        ) == [12]
    assert len(default_queries) == 0

def test_join_proxy_content_types():
    ContentType.objects.clear_cache()
    with CaptureQueriesContext(connections['default']) as default_queries:
        assert list(
            AnimalProxy.objects.using('selected')
            .filter(tags__tag='wanted').values_list('pk', flat=True)
        ) == [12]
        for model, pk in ((ConcreteRelatedModel, 21), (ProxyRelatedModel, 22)):
            assert list(
                model.objects.using('selected')
                .filter(bases__title='wanted').values_list('pk', flat=True)
            ) == [pk]
    assert len(default_queries) == 0

def test_missing_content_type_is_created_on_query_database():
    ContentType.objects.using('selected').filter(pk=selected_type).delete()
    ContentType.objects.clear_cache()
    with CaptureQueriesContext(connections['default']) as default_queries:
        assert not Animal.objects.using('selected').filter(tags__tag='wanted').exists()
    assert len(default_queries) == 0
    assert ContentType.objects.using('selected').filter(
        app_label='relation_probe', model='animal',
    ).exists()

test_join_query_reuse()
test_join_relabeling_and_aggregation()
test_join_proxy_content_types()
test_missing_content_type_is_created_on_query_database()
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
