"""Regression coverage for projects-owned container access inheritance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import transaction
from django.test import override_settings
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model

from angee.compose.permissions import apply_schema_paths, extension_source_map
from angee.fs import write_atomic
from angee.projects.access import bind, unbind
from tests.conftest import (
    Backend,
    Drive,
    File,
    Folder,
    Vendor,
    installed_field_owners,
)
from tests.messaging_models import Channel, Message, Thread
from tests.projects_models import Project, ProjectBinding


def test_project_and_messaging_schemas_declare_the_complete_cascade() -> None:
    """Project grants reach containers, threads, and their messages through native arrows."""

    projects = Path(apps.get_app_config("projects").path, "permissions.extends.zed").read_text()
    messaging = Path(apps.get_app_config("messaging").path, "permissions.zed").read_text()

    for definition in (
        "storage/drive", "storage/folder", "storage/file", "integrate/integration", "messaging/thread",
        "knowledge/vault", "knowledge/record_binding",
    ):
        assert f"definition {definition}" in projects
    assert "relation channel: integrate/integration // rebac:field=channel" in messaging
    assert "relation thread: messaging/thread // rebac:field=thread" in messaging


@pytest.fixture
def project_access_schema(tmp_path: Path, transactional_db: None) -> Any:
    """Load composed permission extensions and restore app schema paths afterward."""

    configs = list(apps.get_app_configs())
    originals = {config: getattr(config, "rebac_schema", None) for config in configs}
    sources = extension_source_map(configs, field_owners=installed_field_owners(configs))
    runtime = tmp_path / "project-access-runtime"
    for relative, source in sources.items():
        write_atomic(runtime / relative, source)
    apply_schema_paths(configs, runtime, sources=sources)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        for config, original in originals.items():
            if original is None:
                if hasattr(config, "rebac_schema"):
                    delattr(config, "rebac_schema")
            else:
                config.rebac_schema = original


@pytest.mark.django_db(transaction=True)
@override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
def test_project_binding_grants_and_revokes_thread_message_access(
    project_access_schema: Any,
) -> None:
    """A project editor writes a bound thread's message and loses access after unbind."""

    del project_access_schema
    user_model = apps.get_model("iam", "User")
    owner = user_model.objects.create_user(username="project-owner")
    editor = user_model.objects.create_user(username="project-editor", kind="service")
    with actor_context(owner):
        project = Project.objects.create(title="Cascade")
    with system_context(reason="tests.project_access.channel"):
        vendor = Vendor.objects.create(slug="project-channel", display_name="Project Channel")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
    with actor_context(owner):
        thread = Thread.objects.create(channel=channel)
        message = Message.objects.create(thread=thread)
        binding = bind(project=project, target=channel)
        assert bind(project=project, target=channel).pk == binding.pk
        assert ProjectBinding.objects.filter(pk=binding.pk, project=project).exists()
        assert not active_relationship_model().objects.filter(
            resource_type="integrate/integration",
            resource_id=str(channel.pk),
            relation="project",
            subject_type="projects/project",
            subject_id=str(project.pk),
        ).exists()
        write_relationships(
            [RelationshipTuple(to_object_ref(project), "editor", to_subject_ref(editor))]
        )
    with system_context(reason="tests.project_access.folder"):
        storage_backend = Backend.objects.create(
            slug="project-folder",
            label="Project folder",
            backend_class="local",
        )
        drive = Drive.objects.create(
            backend=storage_backend,
            slug="project-folder",
            name="Project folder",
        )
        folder = Folder.objects.create(drive=drive, name="Project files", owner=owner)
    with actor_context(owner):
        project.folder = folder
        project.save(update_fields=("folder", "updated_at"))
        bind(project=project, target=folder)
        project.folder = None
        project.save(update_fields=("folder", "updated_at"))
    assert folder.with_actor(editor).has_access("write")
    unbind(project=project.with_actor(owner), target=folder.with_actor(owner))
    assert not folder.with_actor(editor).has_access("write")
    assert channel.with_actor(editor).has_access("write")
    assert thread.with_actor(editor).has_access("write")
    assert message.with_actor(editor).has_access("read")
    assert message.with_actor(editor).has_access("write")
    with actor_context(owner):
        rolled_back = Thread.objects.create()
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                bind(project=project, target=rolled_back)
                assert rolled_back.with_actor(editor).has_access("write")
                raise RuntimeError("rollback")
        assert not rolled_back.with_actor(editor).has_access("write")
        # Seed pre-upgrade evidence directly: native writes reject backed relations.
        leftover = active_relationship_model().objects.create(
            resource_type="messaging/thread",
            resource_id=str(rolled_back.pk),
            relation="project",
            subject_type="projects/project",
            subject_id=str(project.pk),
        )
    assert not rolled_back.with_actor(editor).has_access("write")
    assert active_relationship_model().objects.filter(pk=leftover.pk).exists()
    assert channel.with_actor(editor).has_access("write")
    with transaction.atomic():
        unbind(project=project.with_actor(owner), target=channel.with_actor(owner))
        assert not channel.with_actor(editor).has_access("write")
        assert not thread.with_actor(editor).has_access("write")
        assert not message.with_actor(editor).has_access("read")
        assert not message.with_actor(editor).has_access("write")
    assert binding.pk is not None


@pytest.mark.django_db(transaction=True)
@override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
def test_binding_edits_and_deletes_require_authority(project_access_schema: Any) -> None:
    """Binding persistence cannot move or revoke access around either side's policy owner."""

    del project_access_schema
    user_model = apps.get_model("iam", "User")
    owner = user_model.objects.create_user(username="binding-owner")
    outsider = user_model.objects.create_user(username="binding-outsider")
    with actor_context(owner):
        project = Project.objects.create(title="Protected")
    with system_context(reason="tests.project_access.targets"):
        storage_backend = Backend.objects.create(
            slug="binding-targets",
            label="Binding targets",
            backend_class="local",
        )
        drive = Drive.objects.create(
            backend=storage_backend,
            slug="binding-targets",
            name="Binding targets",
        )
        first = Folder.objects.create(drive=drive, name="First", owner=owner)
        second = Folder.objects.create(drive=drive, name="Second", owner=owner)
    with actor_context(owner):
        binding = bind(project=project, target=first)
    with actor_context(outsider), pytest.raises(PermissionDenied):
        unbind(project=project, target=first)
    with actor_context(outsider), pytest.raises(PermissionDenied):
        ProjectBinding.objects.filter(pk=binding.pk).delete()
    with actor_context(outsider), pytest.raises(PermissionDenied):
        binding.delete()
    with actor_context(outsider), pytest.raises(PermissionDenied):
        binding.target = second
        binding.save()


@pytest.mark.django_db(transaction=True)
@override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
def test_project_drive_access_reaches_folders_and_files(project_access_schema: Any) -> None:
    """A drive binding supplies the native access chain for every contained row."""

    del project_access_schema
    user_model = apps.get_model("iam", "User")
    owner = user_model.objects.create_user(username="drive-project-owner")
    editor = user_model.objects.create_user(username="drive-project-editor")
    with system_context(reason="tests.project_access.drive"):
        backend = Backend.objects.create(slug="project", label="Project", backend_class="local")
        drive = Drive.objects.create(backend=backend, slug="project", name="Project")
        folder = Folder.objects.create(drive=drive, name="Files")
        file = File.objects.create(
            drive=drive,
            folder=folder,
            filename="notes.txt",
            content_hash="0" * 64,
            storage_path="notes.txt",
        )
        write_relationships(
            [RelationshipTuple(to_object_ref(drive), "editor", to_subject_ref(owner))]
        )
    with actor_context(owner):
        project = Project.objects.create(title="Drive cascade")
        bind(project=project, target=drive)
        write_relationships(
            [RelationshipTuple(to_object_ref(project), "editor", to_subject_ref(editor))]
        )
    with actor_context(editor):
        assert drive.has_access("write")
        assert drive.has_access("share")
        assert folder.has_access("write")
        assert file.has_access("write")


def test_projects_app_ready_does_not_require_composed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Native deletion dispatch starts without looking up composed models or data."""

    def refuse_model_lookup(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Project startup must not look up composed models.")

    monkeypatch.setattr(apps, "get_model", refuse_model_lookup)
    monkeypatch.setattr(apps, "get_models", refuse_model_lookup)
    apps.get_app_config("projects").ready()
