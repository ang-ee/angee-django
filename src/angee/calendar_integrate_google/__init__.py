"""Google Calendar backend for the calendar addon.

Contributes only a ``CalendarSource`` backend that syncs every calendar of one
connected Google account into read-only calendars, with the connect verb; no
source models of its own. Selected by the source's ``backend_class``, so the
dependency stays one-way (``calendar_integrate_google`` → ``calendar``). The
account connects through the deployment's ``google`` OAuth client
(``integrate_google``).
"""
