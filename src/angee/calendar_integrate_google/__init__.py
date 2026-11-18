"""Google Calendar backend for the calendar addon.

Contributes two ``CalendarSource`` backends, with their connect verbs and no
source models of its own: one syncs every calendar of one connected Google account
into read-only calendars, the other publishes local calendars to Google calendars
a service-account key writes (resource calendars). Selected by the source's ``backend_class``, so the
dependency stays one-way (``calendar_integrate_google`` → ``calendar``). The
account connects through the deployment's ``google`` OAuth client
(``integrate_google``).
"""
