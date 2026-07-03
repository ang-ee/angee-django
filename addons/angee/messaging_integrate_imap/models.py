"""No source models — this addon contributes only the IMAP channel backend.

The empty module is the stable discovery target for source-model loading; the
``imap`` backend is registered through ``autoconfig.py`` and the ``Channel``
model it serves lives in ``angee.messaging``.
"""

from __future__ import annotations
