"""A native source fixture with no permission provider."""

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.base.models.base import AngeeDataModel
from angee.base.models.refs import RecordRefMixin


class SourceEntry(AngeeDataModel, concrete=True):
    """Neutral source declaration for native optional-IAM composition."""

    name = models.CharField(max_length=80)

    class Meta:
        abstract = True
        app_label = "permissionless"


class Entry(SourceEntry):
    """Concrete source-fixture row; native pytest owns its table."""

    class Meta:
        abstract = False
        app_label = "permissionless"


class PlainEntry(models.Model):
    name = models.CharField(max_length=80)

    class Meta:
        app_label = "permissionless"


class Reference(RecordRefMixin, AngeeDataModel):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey()
    on_target_delete = models.CASCADE

    class Meta:
        abstract = False
        app_label = "permissionless"


class PlainDefaultTarget(models.Model):
    slug = models.SlugField(unique=True)

    class Meta:
        app_label = "permissionless"


class PlainDefaultSource(models.Model):
    target = models.ForeignKey(PlainDefaultTarget, on_delete=models.CASCADE)

    class Meta:
        app_label = "permissionless"
