"""Settings for the notes example host."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from angee.base.settings import compose_defaults

BASE_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BASE_DIR.parents[1]

SECRET_KEY = "notes-example-dev-key"
DEBUG = True
ALLOWED_HOSTS = ["*"]

# The host owns where runtime and data live. angee dev may export
# ANGEE_RUNTIME_DIR / ANGEE_DATA_DIR to point them at its own control
# directory; otherwise anchor to the in-repo .angee via __file__ so manage.py
# works from anywhere in the repo. compose_defaults just uses what it is given.
RUNTIME_DIR = Path(
    os.environ.get("ANGEE_RUNTIME_DIR", BASE_DIR / "src" / "runtime")
)
DATA_DIR = Path(
    os.environ.get("ANGEE_DATA_DIR", REPO_ROOT / ".angee" / "data")
)

# The host owns making the generated ``runtime`` package importable.
if str(RUNTIME_DIR.parent) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR.parent))

COMPOSED_SETTINGS = compose_defaults(
    addons=("example.notes",),
    runtime_dir=RUNTIME_DIR,
    data_dir=DATA_DIR,
    root_urlconf="host.urls",
    asgi_application="host.asgi.application",
    debug=DEBUG,
)
globals().update(COMPOSED_SETTINGS)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
    }
}
