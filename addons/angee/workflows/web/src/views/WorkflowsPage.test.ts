import { describe, expect, test } from "vitest";

import { WORKFLOW_CATALOGUE_FIELDS, publicationLabel } from "./WorkflowsPage";

const t = (key: string, values?: Record<string, unknown>) =>
  key === "publication.published"
    ? `Published v${String(values?.version)}`
    : key === "publication.retired"
      ? "Retired"
      : "Unpublished";

describe("workflow catalogue projection", () => {
  test("requests the derived version used by its publication cell", () => {
    expect(WORKFLOW_CATALOGUE_FIELDS).toContain("current_published_version");
  });

  test("presents published, retired, and never-published heads truthfully", () => {
    expect(publicationLabel({ id: "1", publication_status: "published", current_published_version: 3 }, t as never)).toBe("Published v3");
    expect(publicationLabel({ id: "2", publication_status: "archived", current_published_version: null }, t as never)).toBe("Retired");
    expect(publicationLabel({ id: "3", publication_status: "unpublished", current_published_version: null }, t as never)).toBe("Unpublished");
  });
});
