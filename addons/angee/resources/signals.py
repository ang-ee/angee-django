"""Native lifecycle boundaries for a complete resource load."""

from django.dispatch import Signal

pre_load = Signal()
"""Enter optional addon contexts around one atomic resource batch.

Receivers receive the ledger model as sender and the batch's ExitStack as
``contexts``. A receiver failure aborts the load; context cleanup occurs after
the batch transaction, including dry-run rollback and failed validation.
"""
