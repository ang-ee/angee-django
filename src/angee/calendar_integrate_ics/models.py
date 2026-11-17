"""No source models: this addon contributes only the ICS calendar source backend.

The empty module is the stable discovery target for source-model loading; the
``ics`` backend is registered through ``autoconfig.py`` and the ``CalendarSource``
model it serves lives in ``angee.calendar``.
"""

from __future__ import annotations
