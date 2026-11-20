"""Canonical concrete web forms models for the bare-Django test runtime.

One concrete model per resource type, registered from conftest before Django
creates the test database.
"""

from angee.webforms.models import Form as AbstractForm
from angee.webforms.models import FormAlias as AbstractFormAlias
from angee.webforms.models import FormVersion as AbstractFormVersion
from angee.webforms.models import Response as AbstractResponse
from angee.webforms.models import ResponseRecord as AbstractResponseRecord
from angee.webforms.models import WebformsRole as AbstractWebformsRole


class Form(AbstractForm):
    """Concrete form used by web forms tests."""

    class Meta(AbstractForm.Meta):
        """Django model options for the canonical test form."""

        abstract = False
        app_label = "webforms"
        db_table = "test_webforms_form"
        rebac_resource_type = "webforms/form"


class FormVersion(AbstractFormVersion):
    """Concrete form version used by web forms tests."""

    class Meta(AbstractFormVersion.Meta):
        """Django model options for the canonical test form version."""

        abstract = False
        app_label = "webforms"
        db_table = "test_webforms_form_version"
        rebac_resource_type = "webforms/form_version"


class FormAlias(AbstractFormAlias):
    """Concrete form alias used by web forms tests."""

    class Meta(AbstractFormAlias.Meta):
        """Django model options for the canonical test form alias."""

        abstract = False
        app_label = "webforms"
        db_table = "test_webforms_form_alias"
        rebac_resource_type = "webforms/form_alias"


class Response(AbstractResponse):
    """Concrete response used by web forms tests."""

    class Meta(AbstractResponse.Meta):
        """Django model options for the canonical test response."""

        abstract = False
        app_label = "webforms"
        db_table = "test_webforms_response"
        rebac_resource_type = "webforms/response"


class ResponseRecord(AbstractResponseRecord):
    """Concrete response-to-record link used by web forms tests."""

    class Meta(AbstractResponseRecord.Meta):
        """Django model options for the canonical test response record link."""

        abstract = False
        app_label = "webforms"
        db_table = "test_webforms_response_record"
        rebac_resource_type = "webforms/response_record"


class WebformsRole(AbstractWebformsRole):
    """Concrete table-less REBAC anchor for the ``webforms/role`` namespace.

    The composer emits this anchor in the runtime; the bare test host registers
    it too, so the const-backed ``manager`` arm of ``webforms/form`` resolves to
    a deny instead of raising. ``managed = False``: never a table.
    """

    class Meta(AbstractWebformsRole.Meta):
        """Django model options for the canonical test web forms role anchor."""

        abstract = False
        managed = False
        app_label = "webforms"
        rebac_resource_type = "webforms/role"
