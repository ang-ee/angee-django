"""Foundation test support; requires pytest and pytest-django.

Register ``angee.base.testing.fixtures`` in the test suite's root ``pytest_plugins``:
it binds the installed permission inputs and synchronizes REBAC once per session, and
every test requesting pytest-django's ``db`` rolls back over that baseline.
``composed_permissions`` binds a test-owned copy of the inputs for a test that edits
one.
``angee.base.testing.queries`` classifies captured SQL by its outer statement,
including reads preceded by CTEs or PostgreSQL cursor declarations.
This package is not a Django app and does not define models.
Serving code must not import test support.
"""
