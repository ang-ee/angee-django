"""No source models: this addon contributes only the Google Calendar source backend.

The empty module is the stable discovery target for source-model loading; the
``google`` backend is registered through ``autoconfig.py`` and the
``CalendarSource`` model it serves lives in ``angee.calendar``.
"""

from __future__ import annotations
