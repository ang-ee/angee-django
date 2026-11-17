"""Migration template: address books become records, each folder's ``source_href`` its link.

Copy this module into the project's ``runtime/parties/migrations/``, replace the
placeholder dependencies with the project's own explicit leaves, and keep its
bytes. It runs after the integrate migration that adds ``SyncStream.container``
and before the folder column is dropped (the operations below).

Each folder with a ``source_href`` is adopted into its directory's
``addressbooks`` stream: a ``RecordLink`` whose external key is the href targets
the folder, and every generation of the matching contacts stream (same directory,
partition = the href) names that link as its container. No folder, contact,
contact link or cursor changes, so nothing is downloaded again. The adopted link
carries no observation yet, so the next sync applies each listed address book
once, which states its name on the folder. Running it again adopts nothing twice.
A folder whose href is longer than a link's external key could never have synced
(its contacts stream could not exist): it is skipped and reported, never refused.
The reverse restores ``source_href`` from the adopted links' external keys.

Historical models keep ``Meta.ordering`` on ``sqid``, which migration state does
not carry, so every update clears the ordering.
"""

import logging

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

ADDRESSBOOKS = "addressbooks"

logger = logging.getLogger(__name__)


def adopt_addressbooks(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Adopt each folder's href as its address-book link and the container of its contacts stream."""

    alias = schema_editor.connection.alias
    folders = apps.get_model("parties", "Folder")._base_manager.using(alias)
    streams = apps.get_model("integrate", "SyncStream")._base_manager.using(alias)
    links = apps.get_model("integrate", "RecordLink")._base_manager.using(alias)
    longest = links.model._meta.get_field("external_key").max_length
    folder_type, _ = (
        apps.get_model("contenttypes", "ContentType")
        ._base_manager.using(alias)
        .get_or_create(app_label="parties", model="folder")
    )
    for folder in folders.exclude(source_href="").filter(directory_id__isnull=False).order_by("pk"):
        if len(folder.source_href) > longest:
            logger.warning(
                "Folder %s keeps no address-book link: its href is longer than %s characters and never synced.",
                folder.pk,
                longest,
            )
            continue
        parent = (
            streams.filter(integration_id=folder.directory_id, key=ADDRESSBOOKS, partition="")
            .order_by("-generation")
            .first()
        )
        if parent is None:
            parent = streams.create(
                integration_id=folder.directory_id,
                key=ADDRESSBOOKS,
                partition="",
                kind="record_replica",
                direction="pull",
            )
        link = links.filter(stream_id=parent.pk, external_key=folder.source_href).first()
        if link is None:
            link = links.create(
                stream_id=parent.pk,
                external_key=folder.source_href,
                target_content_type_id=folder_type.pk,
                target_object_id=str(folder.pk),
            )
        streams.filter(
            integration_id=folder.directory_id, key="contacts", partition=folder.source_href
        ).order_by().update(container_id=link.pk)


def restore_source_hrefs(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Write each adopted address book's external key back to its folder's ``source_href``."""

    alias = schema_editor.connection.alias
    folders = apps.get_model("parties", "Folder")._base_manager.using(alias)
    links = apps.get_model("integrate", "RecordLink")._base_manager.using(alias)
    adopted = links.filter(
        stream__key=ADDRESSBOOKS,
        target_content_type__app_label="parties",
        target_content_type__model="folder",
    ).order_by("pk")
    for link in adopted.values("target_object_id", "external_key"):
        folders.filter(pk=link["target_object_id"]).order_by().update(source_href=link["external_key"])


class Migration(migrations.Migration):
    # Placeholders: replace with this project's explicit leaves, the parties migration
    # this follows and the integrate migration that adds ``SyncStream.container``.
    dependencies = [
        ("parties", "0000_parties_leaf"),
        ("integrate", "0000_syncstream_container"),
    ]
    operations = [
        migrations.RunPython(adopt_addressbooks, restore_source_hrefs),
        migrations.RemoveConstraint("folder", "uq_folder_directory_source"),
        migrations.RemoveField("folder", "source_href"),
    ]

