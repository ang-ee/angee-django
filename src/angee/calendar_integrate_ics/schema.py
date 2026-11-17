"""The ICS subscribe mutation this addon contributes."""

from typing import cast

import strawberry

from angee.calendar.schema import CalendarSourceType
from angee.calendar_integrate_ics.connect import subscribe_calendar
from angee.graphql.actions import session_user


@strawberry.type
class IcsCalendarMutation:
    """Subscribe to the protocol owned by this addon."""

    @strawberry.mutation
    def subscribe_ics_calendar(self, info: strawberry.Info, name: str, url: str) -> CalendarSourceType:
        """Probe an ICS feed and retain it as the signed-in user's read-only calendar subscription.

        Any signed-in user subscribes (``session_user`` refuses anyone else); the
        source and its calendar are theirs, and the feed must be a public address.
        """

        return cast(CalendarSourceType, subscribe_calendar(session_user(info), name=name, url=url))


schemas = {"console": {"mutation": [IcsCalendarMutation]}}
