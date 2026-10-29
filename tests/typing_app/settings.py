"""Native Django typing without a composer app or database access."""

from pathlib import Path

from django.apps import AppConfig

from angee.conf import prepend_import_paths

# The optional plugin is source code here, rather than an installed distribution.
prepend_import_paths((Path(__file__).resolve().parents[2] / "addons",))

class BareAngeeConfig(AppConfig):
    name = "angee"


SECRET_KEY = "typing-contracts"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rebac",
    "tests.typing_app.settings.BareAngeeConfig",
    "angee.base",
    "tests.typing_app",
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ANGEE_CHANGESET_MODEL = "typing_app.TypingChangeset"

ANGEE_TYPING_IMPL_CLASSES = {"local": "tests.typing_app.models.TypingImplementation"}
