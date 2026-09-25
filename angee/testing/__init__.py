"""Framework-generic test support; requires pytest and pytest-django.

Register ``angee.testing.fixtures`` in the test suite's root ``pytest_plugins``
to use ``composed_tables`` for native test-database isolation and REBAC permission
synchronization. This package is not a Django app and does not define models.
Serving code must not import test support.
"""
