import { defineBaseAddon } from "@angee/app";
import { SOURCES_TOOLBAR } from "@angee/calendar";

import { ConnectGoogleCalendarAction } from "./ConnectGoogleCalendarAction";
import { enGoogleCalendarMessages } from "./i18n";

export default defineBaseAddon({
  id: "calendar_integrate_google",
  i18n: { calendar_integrate_google: enGoogleCalendarMessages },
  containers: {
    [SOURCES_TOOLBAR]: {
      "calendar_integrate_google.connect": { sequence: 20, content: <ConnectGoogleCalendarAction /> },
    },
  },
});
