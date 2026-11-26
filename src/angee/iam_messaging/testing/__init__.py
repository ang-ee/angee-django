"""Concrete emailed sign-in link composition for source-addon test suites.

Import ``angee.iam_messaging.testing.models`` after Django setup and before test
database setup, with this addon installed. The bare host's user model takes the
``UserSignInLinks`` donor itself. Production settings and serving code must not
depend on this test support.
"""
