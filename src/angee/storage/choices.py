"""Native choices owners for storage."""

from django.db import models


class FileVisibility(models.TextChoices):
    """Whether file access inherits its containers or its attached records."""

    INHERITED = "inherited", "Inherited"
    RECORD = "record", "Record"


class UploadState(models.TextChoices):
    """File byte lifecycle.

    DRAFT rows hold a reserved ``storage_path`` whose backend bytes may not
    exist yet; READY rows have verified bytes, so ``content_hash`` and
    ``size_bytes`` are authoritative; FAILED rows are retained for audit and
    user feedback after a rejected upload.

    Module-scoped (not nested on :class:`File`) because :class:`FileManager`
    is defined before the model and the ``storage_prune`` command consumes
    it without loading a concrete model.
    """

    DRAFT = "draft", "Draft"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"
