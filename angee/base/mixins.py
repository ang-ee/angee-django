"""Historical alias of the ownership deletion handler for generated migrations.

``retained_set_null`` is owned by :mod:`angee.models.ownership`; ``audit_set_null`` was its
earlier name in released migration bodies.
"""

from angee.models.ownership import retained_set_null  # noqa: F401

audit_set_null = retained_set_null
