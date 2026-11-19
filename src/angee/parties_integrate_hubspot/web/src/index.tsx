import { defineBaseAddon } from "@angee/app";
import { DIRECTORIES_TOOLBAR, DIRECTORY_CREATE } from "@angee/parties";

import { ConnectHubSpotAction } from "./ConnectHubSpotAction";
import { enHubSpotMessages } from "./i18n";

export default defineBaseAddon({
  id: "parties_integrate_hubspot",
  i18n: { parties: enHubSpotMessages },
  containers: {
    [DIRECTORIES_TOOLBAR]: {
      "parties_integrate_hubspot.connect": { sequence: 20, requires: DIRECTORY_CREATE, content: <ConnectHubSpotAction /> },
    },
  },
});
