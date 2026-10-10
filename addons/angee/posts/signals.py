"""Public feeds begin their live-event horizon at committed binding finish."""

from django.apps import apps
from rebac import system_context

from angee.integrate.signals import binding_finished


def stamp_live_since(sender, *, instance, **kwargs):
    """Resolve a feed subtype without adding posts policy to integrate."""

    with system_context(reason="posts.binding.finished"):
        feed = apps.get_model("posts", "Feed")._base_manager.filter(pk=instance.pk).first()
        if feed is not None:
            feed.binding_finished()


binding_finished.connect(stamp_live_since, dispatch_uid="posts.binding.live_since")
