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
"""
