"""No database access or composer: isolate native Django type checking."""

SECRET_KEY = "typing-contracts"
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "rebac", "angee", "tests.typing_app"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ANGEE_CHANGESET_MODEL = "typing_app.TypingChangeset"
