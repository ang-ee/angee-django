"""Django config for the Angee tags addon.

Tags use ``ready()`` to judge category maximums around every record merge.
"""

from __future__ import annotations

from django.apps import AppConfig


class TagsConfig(AppConfig):
    """Source app manifest for the Angee tags addon."""

    default = True
    name = "angee.tags"

    def ready(self) -> None:
        """Connect the merge judgement after app population."""

        super().ready()
        from angee.base.signals import pre_merge
        from angee.tags.signals import judge_merged_tags

        pre_merge.connect(judge_merged_tags, dispatch_uid="tags.judge_merged_tags")
