"""Historical migration import preserved through Django's native field.

Released runtime history (``integrate/0010``) serializes ``DiscrepancyOpenField``;
this alias keeps that history importable under the migration policy. It is not an
active field: current models declare ``GeneratedField`` directly.
"""

from django.db.models import GeneratedField

DiscrepancyOpenField = GeneratedField
"""Released migrations serialize the expression, output field and persistence mode."""
