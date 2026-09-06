"""Move VCS model identities after their state has been adopted by integrate_vcs."""

from __future__ import annotations

import json

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import migrations
from django.db.migrations.state import ProjectState

MOVED = ("vcsbridge", "repository", "source", "template")
OLD_LABELS = {f"integrate.{name}" for name in MOVED}
RESOURCE_LABELS = {
    "integrate.VcsBridge": "integrate_vcs.VcsBridge",
    "integrate.Repository": "integrate_vcs.Repository",
    "integrate.Source": "integrate_vcs.Source",
    "integrate.Template": "integrate_vcs.Template",
}
REBAC_TYPES = {
    "integrate/vcs_bridge": "integrate_vcs/vcs_bridge",
    "integrate/repository": "integrate_vcs/repository",
    "integrate/source": "integrate_vcs/source",
    "integrate/template": "integrate_vcs/template",
}
LOWER_MODEL_LABELS = {old.lower(): new.lower() for old, new in RESOURCE_LABELS.items()}
MENU_IDS = {
    "integrate.sources.group": "integrate_vcs",
    "integrate.sources": "integrate_vcs.sources",
    "integrate.templates": "integrate_vcs.templates",
    "integrate.repositories": "integrate_vcs.repositories",
    "integrate.vcs": "integrate_vcs.vcs",
}


def applies(project_state: ProjectState) -> bool:
    """Run while both old and adopted VCS model states coexist."""

    old = {("integrate", name) for name in MOVED}
    new = {("integrate_vcs", name) for name in MOVED}
    models = set(project_state.models)
    if not old & models and not new & models:
        return False
    if old <= models and not new & models:
        return False
    if old <= models and new <= models:
        return True
    if new <= models and not old & models:
        return False
    raise ImproperlyConfigured("angee.integrate_vcs:migrate_vcs_identities found a partial VCS model adoption")


def _model(apps, app_label: str, model_name: str):
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def migrate_vcs_identities(apps, schema_editor) -> None:
    """Rename exact model identities while preserving row and foreign-key identities."""

    alias = schema_editor.connection.alias
    user_app, user_name = settings.AUTH_USER_MODEL.split(".", 1)
    user = _model(apps, user_app, user_name)
    has_preferences = user is not None and any(field.name == "preferences" for field in user._meta.get_fields())
    if has_preferences:
        for row in user._base_manager.using(alias).all().order_by().iterator():
            preferences = row.preferences
            favorites = preferences.get("resource-view.favorites") if isinstance(preferences, dict) else None
            models_by_label = favorites.get("models") if isinstance(favorites, dict) else None
            if not isinstance(models_by_label, dict):
                continue
            for old, new in RESOURCE_LABELS.items():
                if old in models_by_label and new in models_by_label:
                    raise ImproperlyConfigured(
                        "angee.integrate_vcs:migrate_vcs_identities found colliding favorite model keys "
                        f"for User pk={row.pk}: {old!r}, {new!r}"
                    )

    content_type = _model(apps, "contenttypes", "ContentType")
    moved_content_type_ids: list[object] = []
    if content_type is not None:
        target = content_type._base_manager.using(alias).filter(app_label="integrate_vcs", model__in=MOVED)
        if target.exists():
            collisions = list(target.order_by().values_list("app_label", "model"))
            raise ImproperlyConfigured(
                "angee.integrate_vcs:migrate_vcs_identities found destination ContentType rows; "
                f"repair collisions before extraction: {collisions}"
            )
        old_types = content_type._base_manager.using(alias).filter(app_label="integrate", model__in=MOVED)
        moved_content_type_ids = list(old_types.order_by().values_list("pk", flat=True))
        old_types.update(app_label="integrate_vcs")

    version = _model(apps, "reversion", "Version")
    if version is not None and moved_content_type_ids:
        versions = version._base_manager.using(alias).filter(content_type_id__in=moved_content_type_ids).order_by()
        for row in versions.iterator():
            try:
                payload = json.loads(row.serialized_data)
            except (TypeError, ValueError) as error:
                raise ImproperlyConfigured(
                    f"angee.integrate_vcs:migrate_vcs_identities found invalid reversion JSON for Version pk={row.pk}"
                ) from error
            if not isinstance(payload, list):
                raise ImproperlyConfigured(
                    f"angee.integrate_vcs:migrate_vcs_identities expected a reversion list for Version pk={row.pk}"
                )
            changed = False
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                label = str(entry.get("model", "")).lower()
                if label in OLD_LABELS:
                    entry["model"] = label.replace("integrate.", "integrate_vcs.", 1)
                    changed = True
            if changed:
                row.serialized_data = json.dumps(payload, separators=(",", ":"))
                row.save(update_fields=["serialized_data"])

    resource = _model(apps, "resources", "Resource")
    if resource is not None:
        for old, new in RESOURCE_LABELS.items():
            resource._base_manager.using(alias).filter(target_model=old).order_by().update(target_model=new)
            if resource._base_manager.using(alias).filter(target_model=old).order_by().exists():
                raise ImproperlyConfigured(
                    f"angee.integrate_vcs:migrate_vcs_identities could not rewrite Resource target {old!r}"
                )

    if has_preferences:
        for row in user._base_manager.using(alias).all().order_by().iterator():
            preferences = row.preferences
            if not isinstance(preferences, dict):
                continue
            favorites = preferences.get("resource-view.favorites")
            models_by_label = favorites.get("models") if isinstance(favorites, dict) else None
            changed = False
            if isinstance(models_by_label, dict):
                for old, new in RESOURCE_LABELS.items():
                    if old in models_by_label:
                        models_by_label[new] = models_by_label.pop(old)
                        changed = True
            chrome = preferences.get("chrome.rail")
            if isinstance(chrome, dict):
                order = chrome.get("order")
                if isinstance(order, list):
                    rewritten_order = [
                        MENU_IDS.get(value, value) if isinstance(value, str) else value for value in order
                    ]
                    if rewritten_order != order:
                        chrome["order"] = rewritten_order
                        changed = True
                default_item = chrome.get("defaultItemId")
                replacement = MENU_IDS.get(default_item) if isinstance(default_item, str) else None
                if replacement is not None:
                    chrome["defaultItemId"] = replacement
                    changed = True
            if changed:
                row.save(update_fields=["preferences"])

    workflow = _model(apps, "workflows", "Workflow")
    if workflow is not None:
        for old, new in LOWER_MODEL_LABELS.items():
            workflow._base_manager.using(alias).filter(subject_declaration=old).order_by().update(
                subject_declaration=new
            )

    trigger = _model(apps, "workflows", "Trigger")
    if trigger is not None:
        for old, new in LOWER_MODEL_LABELS.items():
            trigger._base_manager.using(alias).filter(event_model_label=old).order_by().update(event_model_label=new)
        for row in trigger._base_manager.using(alias).all().order_by().iterator():
            config = row.config
            if not isinstance(config, dict):
                continue
            changed = False
            for key in ("model", "model_label"):
                value = config.get(key)
                replacement = LOWER_MODEL_LABELS.get(str(value).lower()) if isinstance(value, str) else None
                if replacement is not None and value != replacement:
                    config[key] = replacement
                    changed = True
            if changed:
                row.save(update_fields=["config"])

    addon = _model(apps, "platform", "Addon")
    if addon is not None:
        for row in addon._base_manager.using(alias).all().order_by().iterator():
            labels = row.model_labels
            if not isinstance(labels, list):
                continue
            rewritten = [RESOURCE_LABELS.get(value, value) for value in labels]
            if rewritten != labels:
                row.model_labels = rewritten
                row.save(update_fields=["model_labels"])

    for app_label, model_name, fields in (
        ("rebac", "Relationship", ("resource_type", "subject_type")),
        ("rebac", "RebacResource", ("resource_type",)),
        ("rebac", "SchemaDefinition", ("resource_type",)),
    ):
        model = _model(apps, app_label, model_name)
        if model is None:
            continue
        for field in fields:
            for old, new in REBAC_TYPES.items():
                model._base_manager.using(alias).filter(**{field: old}).order_by().update(**{field: new})

    schema_relation = _model(apps, "rebac", "SchemaRelation")
    if schema_relation is not None:
        for row in schema_relation._base_manager.using(alias).all().order_by().iterator():
            allowed = row.allowed_subjects
            if not isinstance(allowed, list):
                continue
            changed = False
            for subject in allowed:
                if not isinstance(subject, dict):
                    continue
                old_type = subject.get("type")
                new_type = REBAC_TYPES.get(old_type) if isinstance(old_type, str) else None
                if new_type is not None:
                    subject["type"] = new_type
                    changed = True
            if changed:
                row.save(update_fields=["allowed_subjects"])


class Migration(migrations.Migration):
    """Apply the irreversible public-identity rename."""

    dependencies = [("integrate_vcs", "__latest__")]
    operations = [migrations.RunPython(migrate_vcs_identities)]
