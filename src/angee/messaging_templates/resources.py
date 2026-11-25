"""Shipped message templates install as baselines."""

from angee.resources.loader import BaselineResource


class MessageTemplateResource(BaselineResource):
    """A shipped message template is a baseline that never states the hashes the model derives."""

    unstated_columns = ("content_hash", "baseline_hash")
