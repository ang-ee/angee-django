"""Small fixtures shared by the storage regression campaign."""

from typing import Any

import pytest
from django.core.files.storage import FileSystemStorage

from angee.storage.backends import LocalBackend


@pytest.fixture(params=("denormalized", "registry"), autouse=True)
def relationship_storage(request: pytest.FixtureRequest, settings: Any) -> str:
    """Exercise the existing composition with either native relationship store."""

    settings.REBAC_LOCAL_BACKEND_STORAGE = request.param
    return str(request.param)


class OverwritingBackend(LocalBackend):
    """Use Django's native overwrite behavior to expose destructive key reuse."""

    def __init__(self, *, backend_config: Any = None) -> None:
        super().__init__(backend_config=backend_config)
        FileSystemStorage.__init__(
            self, location=self.location, base_url=self.base_url, allow_overwrite=True,
        )
