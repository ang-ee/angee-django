import { defineBaseAddon } from "@angee/app";

import { AddonSourceControls } from "./AddonSourceControls";
import { enPlatformIntegrateVcsMessages } from "./i18n";

const platformIntegrateVcs = defineBaseAddon({
  id: "platform_integrate_vcs",
  i18n: { platform: enPlatformIntegrateVcsMessages },
  containers: {
    "platform.addons#toolbar": { "platform_integrate_vcs.sources": { sequence: 10, content: <AddonSourceControls /> } },
  },
});

export default platformIntegrateVcs;
