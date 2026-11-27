"""Reusable concrete decision mail composition for source-addon test suites.

Import ``angee.decisions_messaging.testing.models`` after Django setup and before
test database setup, with the decisions and messaging test models registered first.
Production settings and serving code must not depend on this test support.
"""
