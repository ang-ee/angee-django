"""Historical aliases of the ownership deletion handler and the statement source kinds.

``retained_set_null`` is owned by :mod:`angee.models.ownership`; ``audit_set_null`` was its
earlier name in released migration bodies. ``SourceKind`` is owned by
:mod:`angee.models.statements`; the messaging bridges' released main imports it from here.
"""

from angee.models.ownership import retained_set_null  # noqa: F401
from angee.models.statements import SourceKind  # noqa: F401

audit_set_null = retained_set_null
