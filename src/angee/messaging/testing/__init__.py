"""Reusable concrete messaging composition for source-addon test suites.

Import ``angee.messaging.testing.models`` after Django setup and before test
database setup, with the source addons and their dependencies installed.
Production settings and serving code must not depend on this test support.
"""
