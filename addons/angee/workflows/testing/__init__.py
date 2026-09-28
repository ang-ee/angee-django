"""Opt-in workflow composition and synchronous drivers for addon test suites.

Install ``angee.workflows.testing`` after ``angee.workflows`` and its declared
source-addon dependencies in test ``INSTALLED_APPS``. Import concrete models
from ``angee.workflows.testing.models`` and drivers from
``angee.workflows.testing.drivers``. Supply the concrete ``AUTH_USER_MODEL`` and
``ANGEE_WORKFLOW_STEP_CLASSES`` before model import; the setting's defaults live
in ``angee.workflows.autoconfig``.

Django's native test database setup creates the shared tables. For permission
synchronization, follow the fixture contract in ``angee.testing``. Production
settings and serving code must not depend on this test support.

Pytest suites opt into ``angee.workflows.testing.fixtures`` alongside
``angee.testing.fixtures``. ``execution`` captures task sends and supplies an
acting administrator; ``register_step`` contributes a class through the existing
registry. Bind ``run_factory(workflow, actor=...)`` and use ``.at(node, status=...)``
to reach a node with production execution. ``load_workflow`` accepts a document
or ``addon.name:resource_xref``; ``start_run`` and ``run_until`` also work with
Django's native ``TransactionTestCase``.
"""
