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
        """Retain an ICS feed as the signed-in user's read-only calendar subscription and queue its first sync.

        Any signed-in user subscribes (``session_user`` refuses anyone else), within
        their source quota and subscribe rate; the source and its calendar are theirs.
        Nothing is fetched here: the first sync reads the feed from a public address
        and reports a feed it cannot read.
        """

        return cast(CalendarSourceType, subscribe_calendar(session_user(info), name=name, url=url))


schemas = {"console": {"mutation": [IcsCalendarMutation]}}
