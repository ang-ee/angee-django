"""Projects-owned lifecycle hooks for project-container relationship mirrors."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db.models.signals import class_prepared, post_delete, post_save, pre_save
from rebac import to_object_ref

from angee.base.refs import CanonicalRecordTarget, canonical_record_target
from angee.projects.access import reconcile_on_commit
from angee.projects.models import Project, ProjectBinding

_UNTRACKED = object()


def connect() -> None:
    """Connect mirrors to the concrete composed project models."""

    for model in apps.get_models():
        _bind(model)
    class_prepared.connect(_on_class_prepared, dispatch_uid="projects.access.class_prepared")


def _on_class_prepared(sender: Any, **kwargs: Any) -> None:
    """Bind lifecycle mirrors when a concrete project model is prepared."""

    del kwargs
    _bind(sender)


def _bind(model: Any) -> None:
    """Connect the receivers owned by one concrete project model."""

    try:
        is_project = issubclass(model, Project)
        is_binding = issubclass(model, ProjectBinding)
    except TypeError:
        return
    if model._meta.abstract:
        return
    label = model._meta.label_lower
    if is_project:
        post_save.connect(
            _reconcile_project_folder,
            sender=model,
            dispatch_uid=f"projects.access.project.save.{label}",
        )
    if is_binding:
        pre_save.connect(
            _snapshot_binding,
            sender=model,
            dispatch_uid=f"projects.access.binding.pre_save.{label}",
        )
        post_save.connect(
            _reconcile_binding,
            sender=model,
            dispatch_uid=f"projects.access.binding.save.{label}",
        )
        post_delete.connect(
            _reconcile_deleted_binding,
            sender=model,
            dispatch_uid=f"projects.access.binding.delete.{label}",
        )


def _reconcile_project_folder(
    sender: Any,
    instance: Any,
    created: bool = False,
    raw: bool = False,
    using: str = "default",
    update_fields: Any = None,
    **kwargs: Any,
) -> None:
    """Schedule mirrors for both sides of a Project.folder edit."""

    del sender, kwargs
    if raw or (
        not created and update_fields is not None and not {"folder", "folder_id"}.intersection(update_fields)
    ):
        return
    project_ref = to_object_ref(instance)
    previous_id = getattr(instance, "_projects_previous_folder_id", _UNTRACKED)
    folder_model = apps.get_model("storage", "Folder")
    if previous_id is not _UNTRACKED and previous_id != instance.folder_id:
        previous = folder_model._base_manager.using(using).filter(pk=previous_id).first()
        if previous is not None:
            reconcile_on_commit(
                project_pk=instance.pk,
                project_ref=project_ref,
                target=canonical_record_target(previous),
                using=using,
            )
    if instance.folder_id is not None:
        folder = folder_model._base_manager.using(using).get(pk=instance.folder_id)
        reconcile_on_commit(
            project_pk=instance.pk,
            project_ref=project_ref,
            target=canonical_record_target(folder),
            using=using,
        )


def _binding_key(instance: Any) -> tuple[Any, Any, Any] | None:
    if instance.project_id is None or instance.content_type_id is None or instance.object_id is None:
        return None
    return instance.project_id, instance.content_type_id, instance.object_id


def _snapshot_binding(
    sender: Any,
    instance: Any,
    raw: bool = False,
    using: str = "default",
    update_fields: Any = None,
    **kwargs: Any,
) -> None:
    """Remember the committed binding key before an edit."""

    del kwargs
    key_fields = {"project", "project_id", "content_type", "content_type_id", "object_id"}
    if raw or instance._state.adding or (update_fields is not None and not key_fields.intersection(update_fields)):
        instance._projects_previous_binding = _UNTRACKED
        return
    instance._projects_previous_binding = sender._base_manager.using(using).filter(pk=instance.pk).values_list(
        "project_id", "content_type_id", "object_id"
    ).first()


def _schedule_binding_key(instance: Any, key: tuple[Any, Any, Any] | None, using: str) -> None:
    if key is None:
        return
    project_id, content_type_id, object_id = key
    project_model = apps.get_model("projects", "Project")
    project = project_model._base_manager.using(using).filter(pk=project_id).first()
    if project is None:
        return
    content_type = (
        apps.get_model("contenttypes", "ContentType")
        .objects.db_manager(using)
        .get(pk=content_type_id)
    )
    reconcile_on_commit(
        project_pk=project_id,
        project_ref=to_object_ref(project),
        target=CanonicalRecordTarget(content_type, object_id),
        using=using,
    )


def _reconcile_binding(
    sender: Any,
    instance: Any,
    created: bool = False,
    raw: bool = False,
    using: str = "default",
    update_fields: Any = None,
    **kwargs: Any,
) -> None:
    """Schedule mirrors for both sides of a ProjectBinding edit."""

    del sender, kwargs
    key_fields = {"project", "project_id", "content_type", "content_type_id", "object_id"}
    if raw or (not created and update_fields is not None and not key_fields.intersection(update_fields)):
        return
    previous = getattr(instance, "_projects_previous_binding", _UNTRACKED)
    current = _binding_key(instance)
    if previous is not _UNTRACKED and previous != current:
        _schedule_binding_key(instance, previous, using)
    _schedule_binding_key(instance, current, using)


def _reconcile_deleted_binding(sender: Any, instance: Any, using: str = "default", **kwargs: Any) -> None:
    """Remove a tuple only when no other projects-owned evidence remains."""

    del sender, kwargs
    _schedule_binding_key(instance, _binding_key(instance), using)
