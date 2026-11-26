"""Django config for Angee's parties addon."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from django.apps import AppConfig
from django.core import checks

from angee.base.hooks import check_hooks

if TYPE_CHECKING:
    from django_compose.provision import ProvisionContext

check_settings = partial(check_hooks, ("ANGEE_PARTIES_SHARED_SENDER_PROVIDERS", "ANGEE_PARTIES_SIGNING_PROVIDERS"))


class PartiesConfig(AppConfig):
    """Source app manifest for contacts and handles."""

    default = True
    name = "angee.parties"

    def compose_provision(self, context: ProvisionContext) -> None:
        """Repair handle and organization-domain keys, holders, owners' contact read and party times on every provision.

        They run after the resources load. ``renormalize_handles`` saves again
        every handle's keys (its spelling key and person-matching key) computed
        under another version of the handle's normalization rule
        (``Handle.NORMALIZATION_VERSION``): the whole backlog, since a row with a
        stale key is missed by the lookups that read keys. A live email handle
        that spells the address of another is folded into the older of the two
        and kept as a redirect, each fold in a transaction of its own; a fold the
        merge refuses is logged and left for the next provision. It saves in
        batches of ``HandleManager.REPAIR_LIMIT`` rows, each its own transaction,
        so an interrupted provision keeps what it saved and the next one resumes.
        With no row behind, it costs one indexed query.
        ``renormalize_organization_domains`` writes again each organization's
        domain key that is not its domain under the same domain rule
        (``PartyManager.renormalize_organization_domains``), reading every row in
        batches and writing only a key that differs.
        ``resolve_stale_owners`` states each holder written onto its row, leaves a
        trashed, confirmed or contested one as it is and re-resolves stale ones,
        at most ``HandleManager.REPAIR_LIMIT`` holders a run (past that, the
        provision log says how many remain and the next provision continues);
        ``grant_directory_owner_contact_reads`` grants each directory's owner the
        read of its contacts where missing (connecting grants it);
        ``backdate_parties_from_sources`` moves each party's creation time back to
        the earliest its directory records' stored payloads state
        (``PartyManager.backdate_from_sources``). All are idempotent, so a
        deployment never depends on someone running them.
        """

        context.call("renormalize_handles")
        context.call("renormalize_organization_domains")
        context.call("resolve_stale_owners")
        context.call("grant_directory_owner_contact_reads")
        context.call("backdate_parties_from_sources")

    def ready(self) -> None:
        """Run parties ready-time hooks after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before the models exist; defer.
        from angee.parties import signals
        from angee.parties.checks import check_directory_mappings

        checks.register(check_settings, checks.Tags.models)
        checks.register(check_directory_mappings, checks.Tags.models)

        signals.connect()
