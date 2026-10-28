"""Historical implementation-field import retained for released migrations.

Live field, registry and row-default callers use angee.base owners. Retire after
the documented migration-history cutoff.
"""
from angee.base.fields.implementation import ImplClassField

__all__ = ["ImplClassField"]
