import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";

import {
  enImpactProbeMessages,
} from "./i18n";

const addon = defineBaseAddon({
  id: "impact_probe",
  // The route claims the canonical `<label>.<Model>` resource label: durable
  // manifests never store a bare model name.
  routes: resourcePageRoutes(
    "impact_probe.impactprobe",
    "/impact-probe",
    lazyRouteComponent(() => import("./views/ImpactProbePage"), "ImpactProbePage"),
    "impact_probe.ImpactProbe",
  ),
  menus: {
    "impact_probe": {
      label: "Impact Probe",
      icon: "file",
      route: "impact_probe.impactprobe",
    },
  },
  i18n: {
    "impact_probe": enImpactProbeMessages,
  },
});

export default addon;
