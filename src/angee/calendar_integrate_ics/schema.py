"""The ICS subscribe mutation this addon contributes."""

from typing import cast

import strawberry

from angee.calendar.schema import CalendarSourceType
from angee.calendar_integrate_ics.connect import subscribe_calendar
from angee.graphql.actions import session_user
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES


@strawberry.type
class IcsCalendarMutation:
    """Subscribe to the protocol owned by this addon."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def subscribe_ics_calendar(self, info: strawberry.Info, name: str, url: str) -> CalendarSourceType:
        """Probe an ICS feed and retain it as a read-only calendar subscription."""

        return cast(CalendarSourceType, subscribe_calendar(session_user(info), name=name, url=url))


schemas = {"console": {"mutation": [IcsCalendarMutation]}}
