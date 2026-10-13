"""Opt-in concrete changeset storage for source-addon tests.

Install after ``angee.provenance``. Django's native test database setup owns
the ``provenance.Changeset`` table, just as it owns the resource testing ledger.
Production settings and serving code must not depend on this composition.
"""
