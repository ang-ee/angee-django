"""Foundation test support; requires pytest and pytest-django.

Register ``angee.base.testing.fixtures`` in the test suite's root ``pytest_plugins``:
it binds the installed permission inputs and synchronizes REBAC once per session, and
``composed_tables`` gives each test rollback isolation over that baseline.
``composed_permissions`` binds a test-owned copy of the inputs for a test that edits
one. Suites outside that conftest's scope re-export the fixture once from their own
``conftest.py``::

    from angee.base.testing.fixtures import composed_tables as composed_tables

Individual test modules consume the fixture by parameter without re-importing it.
``angee.base.testing.queries`` classifies captured SQL by its outer statement,
including reads preceded by CTEs or PostgreSQL cursor declarations.
This package is not a Django app and does not define models.
Serving code must not import test support.
"""
