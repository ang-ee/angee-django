"""Canonical concrete project models for bare-Django access-cascade tests."""

from django.db import models

from angee.projects.models import Link as AbstractLink
from angee.projects.models import Milestone as AbstractMilestone
from angee.projects.models import Project as AbstractProject
from angee.projects.models import ProjectBinding as AbstractProjectBinding
from angee.projects.models import Task as AbstractTask
from angee.proposals.models import ProjectProposalAccess, TaskProposalAccess
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


class Task(TaskWork, TaskProposalAccess, AbstractTask):
    """Concrete task carrying the production chatter-wake owner."""

    sqid_prefix = "tpt_"
    immutable_fields = AbstractTask.immutable_fields
    rebac_grantable = {**AbstractTask.rebac_grantable, **TaskProposalAccess.rebac_grantable}

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

    class Meta(AbstractTask.Meta, TaskProposalAccess.Meta):
        abstract = False
        app_label = "projects"
        constraints = (*AbstractTask.Meta.constraints, *TaskProposalAccess.Meta.constraints)
        db_table = "test_projects_task"
        rebac_resource_type = "projects/task"


class Link(AbstractLink):
    """Concrete link carrying the production generic target and query paths."""

    class Meta(AbstractLink.Meta):
        abstract = False
        app_label = "projects"
        db_table = "test_projects_link"
        rebac_resource_type = "projects/link"


class Project(ProjectWork, ProjectProposalAccess, AbstractProject):
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
    """Concrete phase target for project progress and proposal boundaries."""

    class Meta(AbstractMilestone.Meta):
        abstract = False
        app_label = "projects"
        db_table = "test_projects_milestone"
        rebac_resource_type = "projects/milestone"
