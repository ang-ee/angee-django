"""Opt-in Django test app for reusable concrete addon models.

Install ``angee.testing`` after the source addons in test ``INSTALLED_APPS``.
Import models from ``angee.testing.models`` and register the pytest plugin
``angee.testing.fixtures`` to use its ``composed_tables`` fixture. Django's test
database setup creates these tables; the fixture synchronizes their permissions.
Serving framework code must not import this test support package.
"""
