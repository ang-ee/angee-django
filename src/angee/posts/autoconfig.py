"""Settings fragments contributed when the posts addon is installed."""

from __future__ import annotations

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_CHANNEL_BACKEND_CLASSES.feed": str,
        "ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS:append": list[str],
        "ANGEE_POSTS_FEED_BACKEND_CLASSES": dict[str, str],
    },
)

SETTINGS: _Settings = {
    "ANGEE_CHANNEL_BACKEND_CLASSES.feed": "angee.posts.backends.FeedChannelBackend",
    "ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS:append": ["provider_hidden"],
    # Feed backends a ``posts.Feed`` row may select. ``manual`` is the neutral
    # null-object (no source; ``ImplClassField`` requires a non-empty registry).
    # Source addons add their own with a yamlconf dotted key, e.g.
    # ``"ANGEE_POSTS_FEED_BACKEND_CLASSES.youtube"`` from ``posts_integrate_youtube``.
    "ANGEE_POSTS_FEED_BACKEND_CLASSES": {
        "manual": "angee.posts.backends.ManualFeedBackend",
    },
}
