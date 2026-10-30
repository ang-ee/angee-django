import { defineBaseAddon } from "@angee/app";

import { ConnectCardDavAction } from "./ConnectCardDavAction";
import { enCardDavMessages } from "./i18n";

export default defineBaseAddon({
  id: "parties_integrate_carddav",
  i18n: { parties_integrate_carddav: enCardDavMessages },
  containers: {
    "parties.directories#toolbar": {
      "parties_integrate_carddav.connect": { sequence: 10, content: <ConnectCardDavAction /> },
    },
  },
});
