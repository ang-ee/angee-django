"""Concrete resource ledger and seed targets for native Django test database setup."""

from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.resources import models as sources


class Resource(sources.Resource):
    """The canonical resource ledger used by source-addon tests."""

    class Meta(sources.Resource.Meta):
        """Django options for the shared resource test table."""

        abstract = False
        app_label = "resources"
        db_table = "test_resources_resource"


class ContentTypeRow(models.Model):
    """Seed target whose content-type relations take model labels beside an xref relation."""

    name = models.CharField(max_length=40)
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    content_types = models.ManyToManyField(ContentType, blank=True, related_name="+")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
