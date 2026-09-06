import { defineBaseAddon } from "@angee/app";
import { PLATFORM_ADDON_TOOLBAR_SLOT } from "@angee/platform";

import { AddonSourceControls } from "./AddonSourceControls";
import { enPlatformIntegrateVcsMessages } from "./i18n";

const platformIntegrateVcs = defineBaseAddon({
  id: "platform_integrate_vcs",
  i18n: { platform: enPlatformIntegrateVcsMessages },
  slots: [{ slot: PLATFORM_ADDON_TOOLBAR_SLOT, id: "platform_integrate_vcs.sources", sequence: 10, content: <AddonSourceControls /> }],
});

export default platformIntegrateVcs;
