"""Opt-in workflow/decision composition and synchronous drivers for addon test suites.

Install ``angee.workflows.testing`` after ``angee.workflows`` and its declared
source-addon dependencies, including ``angee.decisions``, in test ``INSTALLED_APPS``.
The combined composition includes decisions with the workflow contribution.
Import concrete models
from ``angee.workflows.testing.models`` and drivers from
``angee.workflows.testing.drivers``. Supply the concrete ``AUTH_USER_MODEL`` and
``ANGEE_WORKFLOW_STEP_CLASSES`` before model import; the setting's defaults live
in ``angee.workflows.autoconfig``.

Django's native test database setup creates the shared tables. For permission
synchronization, follow the fixture contract in ``angee.testing``. Production
settings and serving code must not depend on this test support.

Pytest suites opt into ``angee.workflows.testing.fixtures`` alongside
``angee.testing.fixtures``. ``execution`` captures task sends and supplies an
acting administrator.
``workflow_permissions`` composes and synchronizes the installed permission
contributions for non-admin proofs, restoring app-config bindings after each test.
The fixture uses pytest's worker-local temporary directory.
``register_step`` contributes a class through the existing
registry through the ``register_steps`` context manager in ``testing.drivers``,
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
