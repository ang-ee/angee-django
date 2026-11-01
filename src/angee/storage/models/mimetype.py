"""Native mimetype owners for storage."""

from __future__ import annotations

import logging

from django.db import models

from angee.iam.models.base import ProtectedDataModel
from angee.resources.mixins import ResourceLoadMixin

logger = logging.getLogger(__name__)


class MimeType(ResourceLoadMixin, ProtectedDataModel, concrete=True):
    """Reference row for one MIME type.

    The master-tier taxonomy seed is the source of truth; rows are read-only
    at runtime and deliberately carry no REBAC type, like the resource
    ledger, so any caller may read the catalogue.
    """

    catalogue = True
    catalogue_tier = "master"

    sqid_prefix = "mim_"
    mime_type = models.CharField(max_length=200, unique=True)
    category = models.CharField(max_length=32, db_index=True)
    label = models.CharField(max_length=200)
    icon_key = models.CharField(max_length=64, blank=True)

    class Meta:
        """Django model options for MIME types."""

        abstract = True
        ordering = ("mime_type",)
        verbose_name = "MIME type"

    def __str__(self) -> str:
        """Return the canonical MIME string."""

        return self.mime_type
