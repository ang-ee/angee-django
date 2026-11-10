"""Resource post-load hooks must opt into the loader's cooperative contract."""

from django.apps import apps
from django.core import checks
from django.db import models

from angee.resources.mixins import ResourceLoadMixin


def test_resource_load_check_refuses_uncomposed_hooks_including_inherited_hooks() -> None:
    class UncomposedHook(models.Model):
        @classmethod
        def after_resource_load(cls, instances, **kwargs):
            pass

        class Meta:
            abstract = True

    class InvalidResourceHook(UncomposedHook):
        class Meta:
            app_label = "auth"

    class ValidResourceHook(ResourceLoadMixin):
        class Meta:
            app_label = "auth"

    errors = [
        error for error in checks.run_checks(app_configs=[apps.get_app_config("auth")], tags=[checks.Tags.models])
        if error.id == "resources.E001"
    ]
    assert len(errors) == 1
    assert errors[0].obj is InvalidResourceHook
    assert "ResourceLoadMixin" in errors[0].msg
