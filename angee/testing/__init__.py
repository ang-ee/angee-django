"""Framework-generic test support; requires pytest and pytest-django.

Register ``angee.testing.fixtures`` in the test suite's root ``pytest_plugins``
to use ``composed_tables`` for native test-database isolation and REBAC permission
synchronization. Suites outside that conftest's scope re-export the fixture once
from their own ``conftest.py``::

    from angee.testing.fixtures import composed_tables as composed_tables

Individual test modules consume the fixture by parameter without re-importing it.
This package is not a Django app and does not define models.
Serving code must not import test support.
"""
