"""Canonical concrete project models for bare-Django access-cascade tests."""

from copy import deepcopy

from django.db import models

from angee.base.mixins import ImmutableFieldsMixin, OptimisticLockMixin, OwnerMixin
from angee.base.models import AngeeDataModel
from angee.messaging.models import ThreadedModelMixin
from angee.projects.models import Link as AbstractLink
from angee.projects.models import Milestone as AbstractMilestone
from angee.projects.models import Project as AbstractProject
from angee.projects.models import ProjectBinding as AbstractProjectBinding
from angee.projects.models import Task as AbstractTask
from angee.projects.models import TaskManager
from angee.work.models import ProjectWork, TaskWork
from angee.work.models import Queue as AbstractQueue
from angee.work.models import Stage as AbstractWorkStage
from tests import test_sequence  # noqa: F401 -- register the queue's native sequence targets
from tests.spaces_models import Group


class Queue(AbstractQueue, Group):
    """Native work queue shared by task access and lifecycle fixtures."""

    class Meta(AbstractQueue.Meta):
        abstract = False
        app_label = "work"
        db_table = "test_create_work_queue"
        rebac_resource_type = "work/queue"


class Stage(AbstractWorkStage):
    """Native stage target for the queue's default and lifecycle fixtures."""

    class Meta(AbstractWorkStage.Meta):
        abstract = False
        app_label = "work"
        db_table = "test_create_work_stage"
        rebac_resource_type = "work/stage"


class Task(TaskWork, ImmutableFieldsMixin, OwnerMixin, OptimisticLockMixin, ThreadedModelMixin, AngeeDataModel):
    """Concrete task carrying the production chatter-wake owner."""

    sqid_prefix = "tpt_"
    assignee = AbstractTask._meta.get_field("assignee").clone()
    visibility = AbstractTask._meta.get_field("visibility").clone()
    immutable_fields = AbstractTask.immutable_fields
    TaskVisibility = AbstractTask.TaskVisibility
    set_visibility = AbstractTask.set_visibility
    validate_visibility = AbstractTask.validate_visibility
    objects = TaskManager()
    links = deepcopy(AbstractTask._meta.get_field("links"))
    file_attachments = deepcopy(AbstractTask._meta.get_field("file_attachments"))
    knowledge_bindings = deepcopy(AbstractTask._meta.get_field("knowledge_bindings"))

    # Keep the queue and stage fields used by the native work owner. These
    # fixtures do not exercise cycle scheduling.
    cycle = None
    cycle_id = None
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


class Link(AbstractLink):
    """Concrete link carrying the production generic target and query paths."""

    class Meta(AbstractLink.Meta):
        abstract = False
        app_label = "projects"
        db_table = "test_projects_link"
        rebac_resource_type = "projects/link"


class Project(ProjectWork, AbstractProject):
    """Concrete project carrying the production owners and the work team donor."""

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
    """Concrete explicit binding carrying the production authorization owner."""

    class Meta(AbstractProjectBinding.Meta):
        abstract = False
        app_label = "projects"
        db_table = "test_projects_binding"
        rebac_resource_type = "projects/project_binding"


class Milestone(AbstractMilestone):
    """Concrete phase target for the production project's current milestone."""

    class Meta(AbstractMilestone.Meta):
        abstract = False
        app_label = "projects"
        db_table = "test_projects_milestone"
        rebac_resource_type = "projects/milestone"
