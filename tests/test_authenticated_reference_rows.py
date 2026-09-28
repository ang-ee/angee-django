"""Reference reads and parent arrows compile to SQL without wildcard grants."""

import pytest
from django.apps import apps
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.db import models
from rebac import SubjectRef, actor_context, system_context, to_subject_ref
from rebac.backends import backend
from rebac.backends.local_query import LocalQueryScope
from rebac.evaluator import evaluator_scope
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type

from angee.base.checks import check_authenticated_scopes
from angee.portfolio.models import Release as AbstractRelease
from tests.conftest import create_user
from tests.projects_models import Project
from tests.test_portfolio import ProductRow
from tests.test_productivity_deferred_save import Initiative, InitiativeProject, Update
from tests.test_tags import Tag, TagAssignment
from tests.test_uom import Uom, UomCategory


class ReferenceRelease(AbstractRelease):
    product = models.ForeignKey(ProductRow, on_delete=models.CASCADE, related_name="+")

    class Meta(AbstractRelease.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/release"


@pytest.fixture
def reference_rows(composed_tables):
    with system_context(reason="test.reference.setup"):
        tag = Tag.objects.create(name="Reference")
        category = UomCategory.objects.create(name="Count")
        unit = Uom.objects.create(category=category, name="Each", ratio=1, rounding=1, is_reference=True)
        product = ProductRow.objects.create(name="Reference")
        initiative = Initiative.objects.create(name="Reference")
        project = Project.objects.create(title="Reference")
        placement = InitiativeProject.objects.create(initiative=initiative, project=project)
        update = Update.objects.create(target=initiative, health="on_track", body="Reference")
        release = ReferenceRelease.objects.create(product=product, name="Reference")
        assignment = TagAssignment.objects.create(
            tag=tag, content_type=ContentType.objects.get_for_model(Project), object_id=project.pk,
        )
    return [tag, category, unit, product, initiative, placement, update, release, assignment]


def test_signed_in_reads_include_every_reference_and_the_dependent_arrow(reference_rows, django_assert_num_queries):
    reader = create_user("reference-reader")
    with actor_context(reader), evaluator_scope():
        # Load schema metadata once, as a native request scope does. The budgets
        # below measure predicate compilation and row retrieval, not schema load.
        backend().schema()
        for row in reference_rows:
            with django_assert_num_queries(0):
                predicate = LocalQueryScope(backend(), to_subject_ref(reader), "default").predicate(
                    type(row), "read", model_resource_type(row),
                )
                assert predicate is not None
            with django_assert_num_queries(1):
                matched = list(type(row)._base_manager.filter(predicate, pk=row.pk).values_list("pk", flat=True))
                assert matched == [row.pk]
            # The public scope also checks universal-admin membership for a
            # dependent row. That native fixed overhead is separate from the
            # SQL compiler/execution budget above; exercise its result too.
            assert list(type(row).objects.filter(pk=row.pk).values_list("pk", flat=True)) == [row.pk]
            assert not row.with_actor(reader).has_access("write")
    with actor_context(AnonymousUser()):
        for row in reference_rows:
            assert not type(row).objects.filter(pk=row.pk).exists()


def test_non_user_authenticated_principals_also_read_reference_rows(reference_rows):
    with actor_context(SubjectRef.of("service/principal", "reference-reader")):
        for row in reference_rows:
            assert type(row).objects.filter(pk=row.pk).exists()


def test_reference_inserts_and_updates_never_write_wildcard_readers(reference_rows):
    with system_context(reason="test.reference.inspect"):
        for row in reference_rows:
            row.save()
            assert not active_relationship_model().objects.filter(
                resource_type=model_resource_type(row), resource_id=str(row.pk),
                subject_type="auth/user", subject_id="*",
            ).exists()


def test_reference_schemas_pass_sql_scope_check_without_database_reads(reference_rows, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert check_authenticated_scopes([apps.get_app_config(label) for label in ("tags", "uom", "portfolio")]) == []
