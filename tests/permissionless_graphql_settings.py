"""Native GraphQL source fixture without identity or permission addons."""
from tests.permissionless_settings import *  # noqa: F403
from tests.permissionless_settings import INSTALLED_APPS as BASE_APPS
INSTALLED_APPS = [*BASE_APPS, "channels", "angee.graphql"]
STRAWBERRY_DJANGO = {"MAP_AUTO_ID_AS_GLOBAL_ID": False}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER = True
ROOT_URLCONF = "angee.graphql.urls"
