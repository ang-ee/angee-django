"""No source models — this addon contributes only the operator AddonInstaller backend.

A stable, empty discovery target (the composer imports every addon's ``models``):
the addon's whole contribution is the ``operator`` installer backend wired in via
``autoconfig`` (see ``installer.py`` + ``autoconfig.py``).
"""

from __future__ import annotations
