"""Native model contributions requiring both Projects and Knowledge."""

from typing import Any

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django_compose.models import Composable

from angee.projects.models import ProjectBindingsMixin


class ProjectKnowledge(Composable, extends="projects.Project"):
    """Knowledge-owned project vault setup, composed with the other setup acts."""

    class Meta:
        abstract = True

    def apply_setup(self, *, vault_template: Any = None, **options: Any) -> None:
        """Complete core setup once, then ensure its project knowledge vault."""

        super().apply_setup(**options)
        using = self._state.db
        bindings = apps.get_model("projects", "ProjectBinding").objects.db_manager(using)
        vaults = apps.get_model("knowledge", "Vault").objects.db_manager(using)
        content_type = ContentType.objects.db_manager(using).get_for_model(vaults.model)
        if not bindings.filter(project=self, content_type=content_type).exists():
            if vault_template is None:
                raise ValidationError({"vault_template": "Choose a vault template."})
            vault = vaults.create_from(vault_template, name=self.title, idempotency_key=f"project:{self.pk}")
            bindings.bind(project=self, target=vault)

    @classmethod
    def setup_complete_condition(cls, actor: Any) -> models.Q:
        """Add readable vault-binding evidence to the existing setup condition."""

        bindings = apps.get_model("projects", "ProjectBinding").objects.with_actor(actor).scoped()
        vault = apps.get_model("knowledge", "Vault")
        vault_type_id = ContentType.objects.db_manager(bindings.db).filter(
            app_label=vault._meta.app_label, model=vault._meta.model_name,
        ).values("pk")[:1]
        return super().setup_complete_condition(actor) & models.Q(models.Exists(bindings.filter(
            project_id=models.OuterRef("pk"), content_type_id__in=vault_type_id,
        )))


class VaultProjects(ProjectBindingsMixin, Composable, extends="knowledge.Vault"):
    """Projects-owned reverse collection for knowledge-vault bindings."""

    class Meta:
        abstract = True
