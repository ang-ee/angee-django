"""Settings fragments contributed when the posts addon is installed."""

from __future__ import annotations

SETTINGS = {
    # Feed backends a ``posts.Feed`` row may select. ``manual`` is the neutral
    # null-object (no source; ``ImplClassField`` requires a non-empty registry).
    # Source addons add their own with a yamlconf dotted key, e.g.
    # ``"ANGEE_POSTS_FEED_BACKEND_CLASSES.youtube"`` from ``posts_integrate_youtube``.
    "ANGEE_POSTS_FEED_BACKEND_CLASSES": {
        "manual": "angee.posts.backends.ManualFeedBackend",
    },
}
"""Django settings contributed when the posts addon is installed."""
