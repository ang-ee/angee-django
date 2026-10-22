"""Preserve the field import serialized in released integration migrations.

Those migrations include the expression, output field, and persistence mode, so
Django's native field implements the complete retained contract. Current models
declare ``GeneratedField`` directly.
"""

from django.db.models import GeneratedField

DiscrepancyOpenField = GeneratedField
