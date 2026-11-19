import { defineBaseAddon } from "@angee/app";

import { ConnectHubSpotAction } from "./ConnectHubSpotAction";
import { enHubSpotMessages } from "./i18n";

export default defineBaseAddon({
  id: "parties_integrate_hubspot",
  i18n: { parties_integrate_hubspot: enHubSpotMessages },
  containers: {
    "parties.directories#toolbar": {
      "parties_integrate_hubspot.connect": { sequence: 20, content: <ConnectHubSpotAction /> },
    },
  },
});
