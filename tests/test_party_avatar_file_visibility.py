"""An admitted party cannot disclose a File the viewer cannot read."""

from pathlib import Path

from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.base.signals import mute_changes
from angee.iam import schema as iam_schema
from angee.messaging.testing.models import Party
from angee.parties import schema as parties_schema
from angee.storage import schema as storage_schema
from angee.tags import schema as tags_schema
from tests.conftest import Backend, Drive, File, addon_schema, create_user, execute_schema, result_data


def test_unreadable_avatar_is_absent_from_party_projections(
    composed_tables: None, composed_permissions: None, tmp_path: Path,
) -> None:
    owner, reader = create_user("private-avatar-owner"), create_user("private-avatar-reader")
    with system_context(reason="test.private_avatar.seed"), mute_changes():
        backend = Backend._base_manager.create(
            slug="private-avatar", label="Local", backend_class="local",
            backend_config={"root": str(tmp_path), "base_url": "/media/"},
        )
        drive = Drive._base_manager.create(
            backend=backend, slug="private-avatar-drive", name="Private", prefix="private-avatar",
            created_by=owner,
        )
        avatar = File._base_manager.create(
            drive=drive, filename="secret-face.png", content_hash="b" * 64, owner=owner,
            upload_state="ready", created_by=owner,
        )
        party = Party._base_manager.create(display_name="Shared party", avatar=avatar, owner=owner, created_by=owner)
    write_relationships([RelationshipTuple(to_object_ref(party), "reader", to_subject_ref(reader))])
    assert not File.objects.with_actor(reader).filter(pk=avatar.pk).exists()

    schema = addon_schema(
        iam_schema.schemas, "console", parties_schema.schemas, storage_schema.schemas, tags_schema.schemas,
    )
    query = """{
      parties {
        id display_name
        avatar { id filename mime_type { mime_type } url }
        display_avatar { id filename mime_type { mime_type } url }
      }
    }"""
    data = result_data(execute_schema(schema, query, user=reader))
    shared = next(row for row in data["parties"] if row["display_name"] == "Shared party")
    assert shared["avatar"] is None
    assert shared["display_avatar"] is None

    owner_data = result_data(execute_schema(schema, query, user=owner))
    own = next(row for row in owner_data["parties"] if row["display_name"] == "Shared party")
    assert own["avatar"]["id"] == str(avatar.sqid)
    assert own["avatar"]["filename"] == "secret-face.png"
    assert own["avatar"]["url"]
    assert own["display_avatar"] == own["avatar"]
