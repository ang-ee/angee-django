"""Migration-state guards for the complete registered model collection."""

from django.apps import apps
from django.db.migrations.state import ModelState


def test_registered_children_do_not_redeclare_concrete_parent_fields() -> None:
    """Name any child that repeats a parent's concrete field in migration state."""

    collisions = {}
    for child in apps.get_models():
        local_fields = ModelState.from_model(child).fields.keys()
        for parent in child._meta.parents:
            repeated = sorted(local_fields & {field.name for field in parent._meta.concrete_fields})
            if repeated:
                collisions[f"{child._meta.label} extends {parent._meta.label}"] = repeated
    assert not collisions
