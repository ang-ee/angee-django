// @vitest-environment happy-dom

import type { BaseAddon, CreateAppInput } from "@angee/app";
import {
  captureChrome,
  chromeSnapshotForRoute,
  testGraphQLFetch,
  type ChromeSnapshot,
} from "@angee/app/testing";
import { within } from "@testing-library/react";
import notes from "@angee-example/notes-web";
import iam from "@angee/iam";
import operator from "@angee/operator";
import platform from "@angee/platform";
import { describe, expect, test } from "vitest";

import publicMetadata from "../../runtime/schemas/public.metadata.json";
import consoleMetadata from "../../runtime/schemas/console.metadata.json";

// These are the base-addon manifests composed by the navigation fixture.
const ADDONS: readonly BaseAddon[] = [notes, iam, operator, platform];
const SCHEMAS = {
  public: {
    url: "https://example.test/graphql/public/",
    fetch: testGraphQLFetch,
    metadata: publicMetadata,
  },
  console: {
    url: "https://example.test/graphql/console/",
    fetch: testGraphQLFetch,
    metadata: consoleMetadata,
  },
} satisfies CreateAppInput["schemas"];

describe("addon refine navigation", () => {
  test("projects notes breadcrumbs from refine resources", async () => {
    await expect(chromeFor("/notes")).resolves.toEqual({
      breadcrumbs: [{ label: "Notes", to: "/notes" }],
    });
    await expect(chromeFor("/notes/first")).resolves.toEqual({
      breadcrumbs: [
        { label: "Notes", to: "/notes" },
        { label: "Show" },
      ],
    });
  });

  test("appends the record crumb and links the resource leaf on /notes/$id", async () => {
    const captured = await captureChrome({
      addons: ADDONS,
      path: "/notes/first",
      home: "/notes",
      schemas: SCHEMAS,
    });
    try {
      const renderedTrail = within(captured.host).getByLabelText(
        "Captured breadcrumb trail",
      );
      await within(renderedTrail).findByText("Show");
      const trail = captured.props().trail;
      expect(trail[0]).toEqual({ label: "Notes", to: "/notes" });
      expect(trail).toHaveLength(2);
    } finally {
      captured.cleanup();
    }
  });

  test("projects iam breadcrumbs with linked ancestors", async () => {
    await expect(chromeFor("/iam")).resolves.toEqual({
      breadcrumbs: [
        { label: "IAM", to: "/iam" },
      ],
    });
    await expect(chromeFor("/iam/users")).resolves.toEqual({
      breadcrumbs: [
        { label: "IAM", to: "/iam" },
        { label: "Users", to: "/iam/users" },
      ],
    });
    // OIDC login is no longer a separate IAM page — it's a tab on integrate's OAuth
    // client form (contributed by the iam addon, gated to OIDC provider types).
  });

  test("projects breadcrumbs from the Operator Settings category", async () => {
    await expect(chromeFor("/operator")).resolves.toEqual({
      breadcrumbs: [{ label: "Operator", to: "/operator" }],
    });
    await expect(chromeFor("/operator/services")).resolves.toEqual({
      breadcrumbs: [
        { label: "Operator", to: "/operator" },
        { label: "Services", to: "/operator/services" },
      ],
    });
  });
});

async function chromeFor(path: string): Promise<ChromeSnapshot> {
  return chromeSnapshotForRoute({
    addons: ADDONS,
    path,
    home: "/notes",
    schemas: SCHEMAS,
  });
}
