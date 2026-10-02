"""Reference reads and parent arrows use actor-scoped querysets without wildcard grants."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from rebac import SubjectRef, actor_context, system_context
from rebac.evaluator import evaluator_scope
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type

from angee.portfolio.testing.models import Initiative, InitiativeProject, ProductRow, ReferenceRelease, Update
from angee.projects.testing.models import Project
from angee.tags.testing.models import (
    Tag,
    TagAssignment,
)
from angee.uom.testing.models import (
    Uom,
    UomCategory,
)
from tests.conftest import create_user


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


def test_signed_in_reads_include_every_reference_and_the_dependent_arrow(reference_rows):
    reader = create_user("reference-reader")
    with actor_context(reader), evaluator_scope():
        for row in reference_rows:
            scoped = type(row).objects.with_actor(reader).scoped().filter(pk=row.pk)
            assert list(scoped.values_list("pk", flat=True)) == [row.pk]
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
