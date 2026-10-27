"""Django settings module for composed Angee hosts."""

from __future__ import annotations

from angee.conf import ProjectContract

ProjectContract(globals()).compose()
