"""Opt-in calendar composition for source-addon test suites.

Install ``angee.calendar.testing`` after ``angee.calendar`` and its declared
source-addon dependencies in test ``INSTALLED_APPS``; Django's app population
registers the concrete ``calendar`` models from ``angee.calendar.testing.models``
before the test database is created. The ``parties.Party`` and ``parties.Handle``
targets come from the host's own concrete parties models; the concrete
``CalendarSource`` composes ``angee.integrate.testing.integration.Integration``, so
install ``angee.integrate.testing`` before it. Permission
synchronization follows the fixture contract in ``angee.base.testing``. Production
settings and serving code must not depend on this test support.
"""
