"""Explicit public provenance model declarations and historical import paths."""

from django.apps import apps

from angee.base.models.roles import role_anchor
from angee.provenance.models.changesets import MACHINE_REASON as MACHINE_REASON
from angee.provenance.models.changesets import Changeset as Changeset
from angee.provenance.models.principals import Rule as Rule
from angee.provenance.models.principals import Seed as Seed
from angee.provenance.models.principals import StatementPrincipal as StatementPrincipal
from angee.provenance.statements import SourceKind as SourceKind
from angee.provenance.statements import StatementSourceMixin as StatementSourceMixin

ProvenanceRole = role_anchor("provenance/role")

# Only declare the donor when its actual IAM target participates.
if apps.is_installed("angee.iam"):
    from angee.provenance.contrib.iam.models import UserSource as UserSource
