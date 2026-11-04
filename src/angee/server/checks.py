"""Bootstrap-safe registration of deferred native serving checks."""

from django.core.checks import Error, Tags, register


@register(Tags.urls)
def check_url_collisions(**kwargs: object) -> list[Error]:
    """Load URL policy only when native checks run after setup."""
    from angee.server.urls import check_url_collisions as check

    return check(**kwargs)
