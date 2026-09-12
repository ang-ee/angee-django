"""Nexus adds viewer-relative relationship predicates to Messaging's navigator."""

from typing import Any

from django.db.models import Subquery

from angee.messaging.inbox_navigator import InboxNavigator, InboxNavigatorOptions


class NexusInboxNavigatorOptions(InboxNavigatorOptions):
    """Relationship-aware finder predicates; cadence remains owned by Nexus."""

    fading: bool = False


class NexusInboxNavigator(InboxNavigator):
    """Apply persisted fading facts without teaching Messaging about Nexus ties."""

    options: NexusInboxNavigatorOptions

    def activity(self) -> Any:
        rows = super().activity()
        if not self.options.fading:
            return rows
        viewer = self.parties.model.objects.identity_for_user_id(self.user_id) if self.user_id is not None else None
        if viewer is None:
            return rows.none()
        parties = self.collection("nexus", "Tie").fading_parties_for(viewer)
        return rows.filter(_party__in=Subquery(parties.order_by().values("pk")))
