import { defineBaseAddon } from "@angee/app";
import { DIRECTORIES_TOOLBAR, DIRECTORY_CREATE } from "@angee/parties";

import { ConnectCardDavAction } from "./ConnectCardDavAction";
import { enCardDavMessages } from "./i18n";

export default defineBaseAddon({
  id: "parties_integrate_carddav",
  i18n: { parties: enCardDavMessages },
  containers: {
    [DIRECTORIES_TOOLBAR]: {
      "parties_integrate_carddav.connect": { sequence: 10, requires: DIRECTORY_CREATE, content: <ConnectCardDavAction /> },
    },
  },
});
