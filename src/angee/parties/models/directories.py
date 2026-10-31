"""Native directories model owners for the contacts domain."""

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar, cast

from django.apps import apps
from django.db import models

from angee.base.fields.implementation import ImplClassField
from angee.base.models.base import AngeeDataModel
from angee.base.models.ownership import OwnerMixin
from angee.integrate.models import Bridge, IntegrationCreateMode
from angee.integrate.states import StreamDefinition, StreamDirection, StreamKind
from angee.parties.backends import DirectoryBackend, ParsedAddressbook
from angee.parties.managers import (
    FolderManager,
)
from angee.provenance.statements import (
    SourceKind,
)


class Folder(OwnerMixin, AngeeDataModel, concrete=True):
    """A group of parties — the local mirror of a synced address book.

    The contacts counterpart of storage's ``Drive``/``Folder`` and knowledge's
    ``Vault`` container idea, kept to exactly what sync needs today: the directory
    it mirrors, the collection ``source_href`` (one folder per ``(directory,
    source_href)`` makes the folder upsert idempotent). Sync progress belongs to
    ``integrate.SyncStream``. Owned through its transferable ``owner``, which a
    sync's insert takes from the directory owner it stamps as ``created_by``;
    deleting a folder leaves its linked parties. Manual creation and a folder
    tree (``parent``) are deferred until a create path lands to exercise them.
    """

    sqid_prefix = "fol_"
    name = models.CharField(max_length=200)
    directory = models.ForeignKey(
        "parties.Directory",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="folders",
    )
    source_href = models.CharField(max_length=1024, blank=True, default="")

    objects = FolderManager()

    class Meta:
        """Django model options for the folder source model."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "parties/folder"
        constraints = (
            models.UniqueConstraint(
                fields=("directory", "source_href"),
                condition=~models.Q(source_href=""),
                name="uq_folder_directory_source",
            ),
        )

    def __str__(self) -> str:
        """Return the folder name for Django displays."""

        return self.name


class Directory(Bridge, inherits="integrate.Integration"):
    """A connected contacts source that syncs parties from an external directory.

    An ``integrate.Integration`` child (so it draws its credential / owner / status
    from the connection substrate) and a ``Bridge`` (so the scheduler and the eager
    ``syncIntegration`` mutation drive it). ``backend_class`` selects the protocol —
    ``carddav`` (contributed by ``parties_integrate_carddav``) — and ``config``
    carries the source URL. The inherited ``Bridge.sync()`` drives the backend
    streams; the parties managers own the contact projection and ingest path.
    """

    integration_create_mode: ClassVar[IntegrationCreateMode | None] = IntegrationCreateMode.CONNECT

    @property
    def source_kind(self) -> SourceKind:
        """Contacts asserted by this integration are directory statements."""

        return cast(SourceKind, SourceKind.DIRECTORY)

    backend_class = ImplClassField(
        DirectoryBackend,
        default="manual",
        create_only=True,
    )
    """Registry key for the directory backend bound to this directory."""

    class Meta:
        """Django model options for the directory child model."""

        abstract = True
        ordering = ("-updated_at",)
        verbose_name_plural = "directories"
        rebac_resource_type = "parties/directory"

    def addressbook_stream(self, book: ParsedAddressbook) -> StreamDefinition:
        """Own the discovered folder and its initial contact deletion policy."""

        apps.get_model("parties", "Folder").objects.sync_addressbook(self, book)
        policy = {
            "local_delete": "conflict",
            "remote_delete": "retain",
            **self.config.get("streams", {}).get("contacts", {}).get(book.href, {}),
        }
        if policy["local_delete"] not in {"conflict", "propagate"} or policy["remote_delete"] not in {
            "retain",
            "propagate",
        }:
            raise ValueError("Unknown Directory deletion policy.")
        return StreamDefinition(
            "contacts",
            partition=book.href,
            kind=StreamKind.RECORD_REPLICA,
            direction=StreamDirection.BIDIRECTIONAL,
            reconcile_interval=timedelta(days=1),
            config=policy,
        )

    @property
    def backend(self) -> DirectoryBackend:
        """Return this directory's selected backend, bound to this row."""

        backend_class = self.get_backend_class_impl()
        return backend_class(self)
