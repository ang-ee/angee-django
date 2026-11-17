"""Address books become records: each folder's ``source_href`` becomes its address-book link.

Run after integrate's ``SyncStream.container`` column exists and before the folder
column is dropped (the operations below). Each folder with a ``source_href`` is
adopted into its directory's ``addressbooks`` stream: a ``RecordLink`` whose
external key is the href targets the folder, and every generation of the
matching contacts stream (same directory, partition = the href) names that link
as its container. No folder, contact, contact link or cursor changes, so nothing
is downloaded again. The adopted link carries no observation yet, so the next
sync applies each address book once, which states its name on the folder.

Historical models keep ``Meta.ordering`` on ``sqid``, which migration state does
not carry, so the update clears the ordering.
"""

from django.db import migrations

ADDRESSBOOKS = "addressbooks"


def adopt_addressbooks(apps, schema_editor):
    alias = schema_editor.connection.alias
    folders = apps.get_model("parties", "Folder")._base_manager.using(alias)
    streams = apps.get_model("integrate", "SyncStream")._base_manager.using(alias)
    links = apps.get_model("integrate", "RecordLink")._base_manager.using(alias)
    folder_type, _ = (
        apps.get_model("contenttypes", "ContentType")
        ._base_manager.using(alias)
        .get_or_create(app_label="parties", model="folder")
    )
    for folder in folders.exclude(source_href="").filter(directory_id__isnull=False).order_by("pk"):
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


class Migration(migrations.Migration):
    dependencies = [("parties", "__latest__"), ("integrate", "__latest__")]
    operations = [
        migrations.RunPython(adopt_addressbooks, migrations.RunPython.noop),
        migrations.RemoveConstraint("folder", "uq_folder_directory_source"),
        migrations.RemoveField("folder", "source_href"),
    ]
