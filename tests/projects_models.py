"""Canonical concrete project models for bare-Django access-cascade tests."""

from django.contrib.contenttypes.models import ContentType
from django.db import models

from angee.base.mixins import AuditMixin, SqidMixin
from angee.base.models import AngeeDataModel
from angee.projects.models import Project as AbstractProject
from angee.projects.models import ProjectBinding as AbstractProjectBinding
from angee.work.models import TaskWork


class Task(TaskWork, AuditMixin, AngeeDataModel):
    """Concrete task carrying the production chatter-wake owner."""

    sqid_prefix = "tpt_"

    # This source-model graph exercises chatter wake behavior without composing
    # work's queue lifecycle and its additional model graph.
    queue = None
    stage = None
    cycle = None
    project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
    )

    class Meta:
        abstract = False
        app_label = "projects"
        db_table = "test_projects_task"
        rebac_resource_type = "projects/task"


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

    rebac_grantable = {
        **AbstractProject.rebac_grantable,
        "proposal_viewer": "share",
    }

    class Meta(AbstractProject.Meta):
        abstract = False
        app_label = "projects"
        db_table = "test_projects_project"
        rebac_resource_type = "projects/project"


class ProjectBinding(AbstractProjectBinding):
    """Concrete explicit binding carrying the production reconciliation owner."""

    class Meta(AbstractProjectBinding.Meta):
        abstract = False
        app_label = "projects"
        db_table = "test_projects_binding"
        rebac_resource_type = "projects/project_binding"
