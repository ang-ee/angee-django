import { composeAddons, defineAddon } from "@angee/app";
import { describe, expect, test } from "vitest";

import dashboards from "./index";

const canonicalizer = { canonicalModelLabel: (label: string) => label };

describe("dashboards addon seams", () => {
  test("declares the installed addon-dashboard route by stable key", () => {
    expect(dashboards.routes).toEqual(expect.arrayContaining([expect.objectContaining({
      name: "dashboards.addon",
      path: "/dashboards/addon/$key",
    })]));
  });

  test("composes an addon-owned widget kind into the dashboard registry", () => {
    const kind = {
      id: "arp.pending-decisions",
      contributionId: "arp.accounting-intake.pending-decisions",
      version: 1,
      label: "Pending decisions",
      shape: "rows" as const,
      defaultSize: { w: 4, h: 3 },
      minSize: { w: 2, h: 2 },
      Component: () => null,
    };
    const contributor = defineAddon({
      id: "arp-accounting-intake",
      dashboardWidgetKinds: [kind],
    });

    expect(composeAddons([dashboards, contributor], canonicalizer).dashboards.widgetKinds[kind.id])
      .toBe(kind);
  });
});
