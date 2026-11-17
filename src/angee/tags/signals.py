"""Signal receivers the tags addon connects in :meth:`angee.tags.apps.TagsConfig.ready`."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any

from django.apps import apps
from django.db import models


def judge_merged_tags(
    sender: type[models.Model],
    *,
    survivor: models.Model,
    contexts: ExitStack,
    **kwargs: Any,
) -> None:
    """Judge the category maximum after a merge's moves (``pre_merge``).

    The fold's ``contexts`` exit after the moves, inside the merge transaction, so
    the edge manager's :meth:`~angee.tags.models.TagAssignmentManager.judging_fold`
    refuses a merge that leaves a record over a category's maximum.
    """

    del sender, kwargs
    contexts.enter_context(apps.get_model("tags", "TagAssignment").objects.judging_fold(survivor))
