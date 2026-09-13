"""Canonical concrete project models for bare-Django access-cascade tests."""

from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.base.mixins import SqidMixin
from angee.projects.models import Project as AbstractProject
from angee.projects.models import ProjectBinding as AbstractProjectBinding


class Task(SqidMixin, models.Model):
    """Minimal concrete target required by Project.converted_from."""

    sqid_prefix = "tpt_"

    class Meta:
        app_label = "projects"
        db_table = "test_projects_task"


class Link(SqidMixin, models.Model):
    """Minimal concrete target required by Project.links."""

    sqid_prefix = "tpl_"

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()

    class Meta:
        app_label = "projects"
        db_table = "test_projects_link"


class Project(AbstractProject):
    """Concrete project carrying the production folder lifecycle owner."""

    rebac_grantable = AbstractProject.rebac_grantable

    class Meta(AbstractProject.Meta):
        abstract = False
        app_label = "projects"
        db_table = "test_projects_project"
        rebac_resource_type = "projects/project"
        rebac_id_attr = "sqid"


class ProjectBinding(AbstractProjectBinding):
    """Concrete explicit binding carrying the production reconciliation owner."""

    class Meta(AbstractProjectBinding.Meta):
        abstract = False
        app_label = "projects"
        db_table = "test_projects_binding"
        rebac_resource_type = "projects/project_binding"
        rebac_id_attr = "sqid"


PROJECT_TEST_MODELS = (Task, Link, Project, ProjectBinding, Project.history.model)
