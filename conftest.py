"""Repository-wide test host: the shared source graph and the framework's pytest plugins.

The provenance plugin supplies attribution boundaries explicitly; the generic
framework plugin owns native database setup and permission synchronization.
"""

from tests import conftest as source_models  # noqa: F401 -- register the shared source graph before database setup

pytest_plugins = (
    "angee.base.testing.fixtures", "angee.provenance.testing.fixtures",
    "angee.workflows.testing.fixtures", "angee.parties.testing.fixtures",
)
