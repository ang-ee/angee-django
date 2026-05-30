"""Settings for the notes example host."""

from __future__ import annotations

from pathlib import Path

from angee.base.settings import compose_defaults

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = "notes-example-dev-key"
DEBUG = True
ALLOWED_HOSTS = ["*"]

globals().update(
    compose_defaults(
        addons=("example.notes",),
        runtime_dir=BASE_DIR / "src" / "runtime",
        root_urlconf="host.urls",
        asgi_application="host.asgi.application",
        debug=DEBUG,
    )
)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ANGEE_DATA_DIR / "db.sqlite3",
    }
}
