"""ICS feed backend for the calendar addon.

Contributes only a ``CalendarSource`` backend that subscribes to one iCalendar
feed and polls its events into a read-only calendar, with the subscribe verb and
its console action; no source models of its own. Selected by the source's
``backend_class``, so the dependency stays one-way
(``calendar_integrate_ics`` → ``calendar``).
"""
