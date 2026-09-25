"""Opt-in record-sync composition for addon source test suites.

Install ``angee.integrate.testing`` after ``angee.integrate`` and its declared
source-addon dependencies in test ``INSTALLED_APPS``. Import concrete models
from ``angee.integrate.testing.models``. Supply the suite's concrete
``integrate.Integration``, related models and ``AUTH_USER_MODEL`` before database
setup. Bare settings must supply ``ANGEE_OAUTH_PROVIDER_TYPES`` before model
import; its defaults live in ``angee.integrate.autoconfig``.

Django's native test database setup creates the shared tables. For permission
synchronization, follow the fixture contract in ``angee.testing``. Production
settings and serving code must not depend on this test support.
"""
