// @vitest-environment happy-dom

import type { BaseAddon } from "@angee/base";
import {
  captureChrome,
  chromeSnapshotForRoute,
  type ChromeSnapshot,
} from "@angee/base/testing";
import { within } from "@testing-library/react";
import notes from "@angee-example/notes-web";
import iam from "@angee/iam";
import operator from "@angee/operator";
import platform from "@angee/platform";
import { describe, expect, test } from "vitest";

// `operator` contributes its console into the `platform` app (parentId), so a
// valid composition must include platform — without it the contribution dangles.
const ADDONS: readonly BaseAddon[] = [notes, iam, operator, platform];

describe("addon route chrome", () => {
  test("derives notes chrome from the notes menu", async () => {
    await expect(chromeFor("/notes")).resolves.toEqual({
      title: "Notes",
      icon: "notes",
      breadcrumbs: [{ label: "Notes" }],
    });
    await expect(chromeFor("/notes/first")).resolves.toEqual({
      title: "Notes",
      icon: "notes",
      breadcrumbs: [
        { label: "Notes", to: "/notes" },
        { label: expect.any(Object) },
      ],
    });
  });

  test("appends the record crumb and links the static leaf on /notes/$id", async () => {
    const captured = await captureChrome({
      addons: ADDONS,
      path: "/notes/first",
      home: "/notes",
    });
    try {
      const trail = captured.props().trail;
      expect(trail[0]).toEqual({ label: "Notes", to: "/notes" });
      expect(trail).toHaveLength(2);
      const renderedTrail = within(captured.host).getByLabelText(
        "Captured breadcrumb trail",
      );
      await within(renderedTrail).findByText("Note");
    } finally {
      captured.cleanup();
    }
  });

  test("derives iam chrome with linked ancestors", async () => {
    await expect(chromeFor("/iam")).resolves.toEqual({
      title: "IAM",
      icon: "auth",
      breadcrumbs: [
        { label: "IAM", to: "/iam" },
        { label: "Overview" },
      ],
    });
    await expect(chromeFor("/iam/users")).resolves.toEqual({
      title: "IAM",
      icon: "auth",
      breadcrumbs: [
        { label: "IAM", to: "/iam" },
        { label: "Users" },
      ],
    });
    // OIDC login is no longer a separate IAM page — it's a tab on integrate's OAuth
    // client form (contributed by the iam addon, gated to OIDC provider types).
  });

  test("derives operator chrome nested under the platform app", async () => {
    // Operator contributes into the platform app, so its pages live under the
    // Platform app: the chrome's app identity is Platform (`>_`), and the trail
    // descends Platform › Operator › <section>.
    await expect(chromeFor("/operator")).resolves.toEqual({
      title: "Platform",
      icon: "platform",
      breadcrumbs: [
        { label: "Platform", to: "/platform" },
        { label: "Operator" },
      ],
    });
    await expect(chromeFor("/operator/services")).resolves.toEqual({
      title: "Platform",
      icon: "platform",
      breadcrumbs: [
        { label: "Platform", to: "/platform" },
        { label: "Operator", to: "/operator" },
        { label: "Services" },
      ],
    });
  });
});

async function chromeFor(path: string): Promise<ChromeSnapshot> {
  return chromeSnapshotForRoute({
    addons: ADDONS,
    path,
    home: "/notes",
  });
}
