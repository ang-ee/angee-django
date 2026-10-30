"""Public feeds begin their live-event horizon at committed discovery finish."""

from django.apps import apps
from rebac import system_context

from angee.integrate.signals import discovery_finished


def stamp_live_since(sender, *, instance, **kwargs):
    """Resolve a feed subtype without adding posts policy to integrate."""

    with system_context(reason="posts.discovery.finished"):
        feed = apps.get_model("posts", "Feed")._base_manager.filter(pk=instance.pk).first()
        if feed is not None:
            feed.discovery_finished()


discovery_finished.connect(stamp_live_since, dispatch_uid="posts.discovery.live_since")
