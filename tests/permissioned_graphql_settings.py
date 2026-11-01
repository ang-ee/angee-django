"""Native IAM+GraphQL source fixture with optional contribution declarations."""
from tests.iam_only_settings import *  # noqa: F403
from tests.iam_only_settings import INSTALLED_APPS as BASE_APPS
from angee.iam.autoconfig import SETTINGS
INSTALLED_APPS = [*BASE_APPS, "channels", "angee.graphql"]
STRAWBERRY_DJANGO = {"MAP_AUTO_ID_AS_GLOBAL_ID": False}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER = True
ROOT_URLCONF = "angee.graphql.urls"
for _name, _value in SETTINGS.items():
    if _name.startswith("ANGEE_GRAPHQL_"):
        globals()[_name.removesuffix(":append")] = _value
