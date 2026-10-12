"""Repository-wide test host: the shared source graph and the framework's pytest plugins.

The attribution boundary every test runs under is the ``angee.testing.fixtures``
plugin's ``captured_changeset`` fixture, which consumer hosts load the same way.
"""

from tests import conftest as source_models  # noqa: F401 -- register the shared source graph before database setup

pytest_plugins = ("angee.testing.fixtures", "angee.workflows.testing.fixtures")
