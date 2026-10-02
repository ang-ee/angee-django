"""Canonical concrete tags models for the bare-Django test runtime.

Permission queries resolve the tag assignment backings, so register the models
from conftest before Django creates the test database. One concrete model per
resource type.
"""

from django.db import models

from angee.tags.models import Tag as AbstractTag
from angee.tags.models import TagAssignment as AbstractTagAssignment
from angee.tags.models import TagRole as AbstractTagRole


class Tag(AbstractTag):
    """Concrete tag carrying an ordinary consumer marker unrelated to visibility.

    ``shared_marker`` stands for a field a consumer adds to the shared vocabulary;
    the tags tests pin that it never controls who reads a tag.
    """

    shared_marker = models.BooleanField(default=True)

    class Meta(AbstractTag.Meta):
        """Django model options for the canonical test tag."""

        abstract = False
        app_label = "tags"
        db_table = "test_tags_tag"
        rebac_resource_type = "tags/tag"



class TagAssignment(AbstractTagAssignment):
    """Concrete polymorphic tag edge used by tags tests."""

    class Meta(AbstractTagAssignment.Meta):
        """Django model options for the canonical test tag assignment."""

        abstract = False
        app_label = "tags"
        db_table = "test_tags_assignment"
        rebac_resource_type = "tags/tag_assignment"



class TagRole(AbstractTagRole):
    """Concrete table-less REBAC anchor for the ``tags/role`` namespace.

    The composer emits this anchor in the runtime; the bare test env must
    register it too so the const-backed ``admin`` arm of ``tags/role`` (reached
    on every actor-scoped ``tags/tag`` read through
    ``manager->effective_member``) resolves to a deny instead of raising
    ``SchemaError``. ``managed = False`` — never a table, only a type anchor.
    """

    class Meta(AbstractTagRole.Meta):
        """Django model options for the canonical test tags role anchor."""

        abstract = False
        managed = False
        app_label = "tags"
        rebac_resource_type = "tags/role"
