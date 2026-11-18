"""The Google Calendar connect mutation this addon contributes."""

from typing import cast

import strawberry
from django.apps import apps

from angee.calendar.schema import CalendarSourceType
from angee.calendar_integrate_google.connect import connect_google_calendar, connect_google_calendar_publishing
from angee.graphql.actions import resolve_action_target, session_user
from angee.graphql.ids import PublicID

Credential = apps.get_model("integrate", "Credential")


@strawberry.type
class GoogleCalendarMutation:
    """Connect the protocols owned by this addon: Google accounts to sync in, service-account keys to publish out."""

    @strawberry.mutation
    def connect_google_calendar(self, info: strawberry.Info, credential_id: PublicID) -> CalendarSourceType:
        """Sync the calendars of one of the signed-in user's Google accounts and queue the first sync.

        Any signed-in user connects (``session_user`` refuses anyone else) one of
        their own connected Google accounts that granted Calendar access, within
        their source quota and rate; the source and its calendars are theirs.
        Nothing is fetched here.
        """

        user = session_user(info)
        # One lookup among the user's own accounts: an unknown id and another user's fail alike.
        credential = resolve_action_target(
            Credential,
            credential_id,
            reason="calendar_integrate_google.connect",
            queryset=Credential.objects.connected_for(user),
        )
        return cast(CalendarSourceType, connect_google_calendar(user, credential))

    @strawberry.mutation
    def connect_google_calendar_publishing(self, info: strawberry.Info, credential_id: PublicID) -> CalendarSourceType:
        """Connect one of the signed-in user's service-account keys as a source that publishes calendars.

        The key is the user's own (an unknown id and another user's fail alike), and
        the source is theirs, within their source quota and rate. Calendars are
        published through it with ``publish_calendar``; nothing is sent here.
        """

        user = session_user(info)
        credential = resolve_action_target(
            Credential,
            credential_id,
            reason="calendar_integrate_google.connect_publishing",
            queryset=Credential.objects.owned_by(user),
        )
        return cast(CalendarSourceType, connect_google_calendar_publishing(user, credential))


schemas = {"console": {"mutation": [GoogleCalendarMutation]}}
