"""Django config for Angee's posts addon."""

from __future__ import annotations

from django.apps import AppConfig


class PostsConfig(AppConfig):
    """Source app manifest for the Angee public-post domain.

    The addon owns the public-post surface layered on ``messaging``: external
    content ``Feed``s (``integrate.Integration`` bridges that poll a platform),
    ``FeedFollow`` subscriptions (the following/timeline edge), rolled-up
    ``PostMetrics`` engagement counts, per-actor reactions on the reused
    ``messaging.Reaction`` table, per-integration
    API ``Quota``, and the public-thread fields it contributes onto
    ``messaging.Thread``/``messaging.Message`` through the same-row ``extends``
    seam. It never forks messaging and reuses its ingest write path; feed source
    backends (youtube/facebook) are downstream ``posts_integrate_*`` addons that
    contribute ``FeedBackend`` impls.
    """

    default = True
    name = "angee.posts"

    def ready(self) -> None:
        """Run posts ready-time hooks after app population."""

        super().ready()
