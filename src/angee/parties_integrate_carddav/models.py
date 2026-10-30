"""No source models — this addon contributes only the CardDAV directory backend.

The empty module is the stable discovery target for source-model loading; the
``carddav`` backend is registered through ``autoconfig.py`` and the ``Directory``
model it serves lives in ``angee.parties``.
"""

from __future__ import annotations
