"""Django config for Angee's social addon."""

from __future__ import annotations

from django.apps import AppConfig


class SocialConfig(AppConfig):
    """Source app manifest for the Angee public-social domain.

    The addon owns the public-social surface layered on ``messaging``: external
    content ``Feed``s (``integrate.Integration`` bridges that poll a platform),
    ``FeedFollow`` subscriptions (the following/timeline edge), rolled-up
    ``PostMetrics`` engagement counts, per-actor reactions on the reused
    ``messaging.Reaction`` table, per-account
    API ``Quota``, and the public-thread fields it contributes onto
    ``messaging.Thread``/``messaging.Message`` through the same-row ``extends``
    seam. It never forks messaging and reuses its ingest write path; feed source
    backends (youtube/facebook) are downstream ``social_integrate_*`` addons that
    contribute ``FeedBackend`` impls.
    """

    default = True
    name = "angee.social"

    def ready(self) -> None:
        """Run social ready-time hooks after app population."""

        super().ready()
