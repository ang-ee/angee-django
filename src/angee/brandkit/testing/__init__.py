"""Reusable concrete brand kit composition for source-addon test suites.

Import ``angee.brandkit.testing.models`` after Django setup and before test
database setup, with the source addons and their dependencies installed, after
the module that registers ``storage.File``. Production settings and serving code
must not depend on this test support.
"""
