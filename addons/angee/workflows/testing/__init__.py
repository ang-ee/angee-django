"""Opt-in workflow composition and synchronous drivers for addon test suites.

Install ``angee.workflows.testing`` after ``angee.workflows`` and its declared
source-addon dependencies in test ``INSTALLED_APPS``. Import workflow models
from ``angee.workflows.testing.models`` and drivers from
``angee.workflows.testing.drivers``. Supply the concrete ``AUTH_USER_MODEL`` and
``ANGEE_WORKFLOW_STEP_CLASSES`` before model import; the setting's defaults live
in ``angee.workflows.autoconfig``.

Django's native test database setup creates the shared tables. For permission
synchronization, follow the fixture contract in ``angee.testing``. Production
settings and serving code must not depend on this test support.

Pytest suites opt into ``angee.workflows.testing.fixtures`` alongside
``angee.testing.fixtures``. ``capture_tasks`` captures task sends; ``execution``
also supplies an acting administrator. ``workflow_permissions`` lives in core
``angee.testing.fixtures`` and composes installed permission contributions.
``observe(model)`` collects committed change publications. ``trigger_source(model)``
temporarily opts a model into record-change admission; pass ``connect=True`` to
exercise native save capture. Both are scoped context managers.
``register_step`` contributes a class to the existing registry through the
``register_steps`` context manager in ``testing.drivers``,
also available to ``TransactionTestCase``. Bind ``run_factory(workflow, actor=...)``
and use ``.at(node, status=...)``
to reach a node with production execution. ``load_workflow`` accepts a document
or canonical ``addon.name.xref`` through the native resource adapter. Xref
loading needs a concrete resource ledger; source suites install
``angee.resources.testing``. The resource row owns publication intent, and the
normal demo-tier guard applies unless ``allow_non_dev=True`` is explicit.
``start_run``, ``run_until`` and ``decide`` also work with
Django's native ``TransactionTestCase``.
"""
