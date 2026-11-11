"""Small fixtures shared by the storage regression campaign."""

from typing import Any

from django.core.files.storage import FileSystemStorage

from angee.storage.backends import LocalBackend


class OverwritingBackend(LocalBackend):
    """Use Django's native overwrite behavior to expose destructive key reuse."""

    def __init__(self, *, backend_config: Any = None) -> None:
        super().__init__(backend_config=backend_config)
        FileSystemStorage.__init__(
            self, location=self.location, base_url=self.base_url, allow_overwrite=True,
        )
