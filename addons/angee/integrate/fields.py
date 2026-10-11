"""Historical migration import preserved through Django's native field."""

from django.db.models import GeneratedField

DiscrepancyOpenField = GeneratedField
"""Released migrations serialize the expression, output field and persistence mode."""
