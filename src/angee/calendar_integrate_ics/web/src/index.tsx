import { defineBaseAddon } from "@angee/app";

import { enIcsMessages } from "./i18n";
import { SubscribeIcsCalendarAction } from "./SubscribeIcsCalendarAction";

export default defineBaseAddon({
  id: "calendar_integrate_ics",
  i18n: { calendar_integrate_ics: enIcsMessages },
  containers: {
    "calendar.sources#toolbar": {
      "calendar_integrate_ics.subscribe": { sequence: 10, content: <SubscribeIcsCalendarAction /> },
    },
  },
});
