import { defineBaseAddon } from "@angee/app";

import { RestartNotice } from "./RestartNotice";
import { enPlatformIntegrateOperatorMessages } from "./i18n";

export default defineBaseAddon({
  id: "platform_integrate_operator",
  i18n: { platformIntegrateOperator: enPlatformIntegrateOperatorMessages },
  containers: {
    "shell#notices": {
      "platform_integrate_operator.restart": { sequence: 10, content: <RestartNotice /> },
    },
  },
});
