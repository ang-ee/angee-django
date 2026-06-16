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
import { describe, expect, test } from "vitest";

const ADDONS: readonly BaseAddon[] = [notes, iam, operator];

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
    // OIDC sign-in providers are a direct child of the IAM rail (the OAuth connect
    // substrate moved to @angee/integrate).
    await expect(chromeFor("/iam/oidc")).resolves.toEqual({
      title: "IAM",
      icon: "auth",
      breadcrumbs: [
        { label: "IAM", to: "/iam" },
        { label: "OIDC Providers" },
      ],
    });
  });

  test("derives operator chrome from the operator menu", async () => {
    await expect(chromeFor("/operator")).resolves.toEqual({
      title: "Operator",
      icon: "operator",
      breadcrumbs: [{ label: "Operator" }],
    });
    await expect(chromeFor("/operator/services")).resolves.toEqual({
      title: "Operator",
      icon: "operator",
      breadcrumbs: [
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
