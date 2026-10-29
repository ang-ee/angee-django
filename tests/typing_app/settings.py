"""Native Django typing without a composer app or database access."""

SECRET_KEY = "typing-contracts"
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rebac",
    "angee.base",
    "tests.typing_app",
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ANGEE_CHANGESET_MODEL = "typing_app.TypingChangeset"

ANGEE_TYPING_IMPL_CLASSES = {"local": "tests.typing_app.models.TypingImplementation"}
