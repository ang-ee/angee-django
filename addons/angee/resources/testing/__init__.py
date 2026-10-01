"""Opt-in concrete resource ledger for addon source test suites.

Install ``angee.resources.testing`` after ``angee.resources`` in test
``INSTALLED_APPS`` and import ``Resource`` from its ``models`` module. Native
Django test database setup owns its table. This gives source-addon consumers
the same ``resources.Resource`` registry identity as a composed host, including
the ledger used by ``load_xref`` and the workflow test driver.

Production settings and serving code must not depend on this test support.
"""
