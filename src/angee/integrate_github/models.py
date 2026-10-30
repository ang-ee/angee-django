"""The GitHub VCS implementation addon contributes no source models.

Its implementation is the :class:`~angee.integrate_github.backend.GitHubBackend`,
resolved per ``VcsBridge`` row through ``backend_class``. This module exists so
addon source-model discovery has a stable, empty target.
"""

from __future__ import annotations
