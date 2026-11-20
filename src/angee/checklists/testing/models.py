"""Canonical concrete checklists models for the bare-Django test runtime.

Permission queries resolve the result and evidence backings, so register the
models from conftest before Django creates the test database. One concrete model
per resource type.
"""

from angee.checklists.models import ChecklistEvidence as AbstractChecklistEvidence
from angee.checklists.models import ChecklistResult as AbstractChecklistResult


class ChecklistResult(AbstractChecklistResult):
    """Concrete checklist result used by checklists tests."""

    class Meta(AbstractChecklistResult.Meta):
        """Django model options for the canonical test checklist result."""

        abstract = False
        app_label = "checklists"
        db_table = "test_checklists_result"
        rebac_resource_type = "checklists/result"


class ChecklistEvidence(AbstractChecklistEvidence):
    """Concrete checklist evidence link used by checklists tests."""

    class Meta(AbstractChecklistEvidence.Meta):
        """Django model options for the canonical test checklist evidence link."""

        abstract = False
        app_label = "checklists"
        db_table = "test_checklists_evidence"
        rebac_resource_type = "checklists/evidence"
