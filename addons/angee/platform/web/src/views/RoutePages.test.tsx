// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { createRouteHref } from "@angee/ui/runtime";

const mocks = vi.hoisted(() => ({
  routeHref: vi.fn(),
}));

vi.mock("nuqs", () => ({
  parseAsString: {},
  useQueryState: () => [null],
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  Badge: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  Chip: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  Code: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  ListView: ({
    columns = [],
    resource,
    rowHref,
  }: {
    columns?: ReadonlyArray<{
      field: string;
      render?: (row: Record<string, unknown>) => React.ReactNode;
    }>;
    resource: string;
    rowHref?: (row: Record<string, unknown>) => string;
  }) => {
    const row: Record<string, unknown> = resource === "platform.Field"
      ? {
          id: "field-1",
          name: "owner",
          kind: "relation",
          model: "notes.Note",
          addon: "example.notes",
          relation_target: "iam.User",
        }
      : resource === "platform.Model"
        ? {
            id: "notes.Note",
            model_name: "Note",
            addon_id: "example.notes",
            addon_label: "Notes",
            db_table: "notes_note",
            field_count: 3,
            relation_count: 1,
            resource_type: "notes.note",
            depends_on: ["iam.User"],
          }
        : { id: "example.notes" };
    return (
      <div data-testid={resource} data-row-href={rowHref?.(row) ?? ""}>
        {columns.map((column) => (
          <span key={column.field}>{column.render?.(row)}</span>
        ))}
      </div>
    );
  },
  statusTone: () => "neutral",
  textRoleVariants: () => "",
  useRouteHref: () => mocks.routeHref,
}));

vi.mock("../i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../i18n")>()),
  usePlatformT: () => (key: string) => key,
}));

vi.mock("../lib/cells", () => ({
  LinkedChips: ({
    href,
    items,
  }: {
    href: (item: string) => string;
    items: readonly string[];
  }) => <>{items.map((item) => <a key={item} href={href(item)}>{item}</a>)}</>,
  TextRouteLink: ({
    children,
    href,
  }: {
    children?: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));

vi.mock("./AddonCard", () => ({
  ADDON_MODEL: "platform.Addon",
  AddonCard: () => null,
  AddonCardActions: () => null,
  SOURCE_TONES: {},
  STATE_TONES: {},
}));

vi.mock("./AddonSourceControls", () => ({
  AddonSourceControls: () => null,
}));

import { AddonsPage } from "./AddonsPage";
import { FieldsPage } from "./FieldsPage";
import { ModelsPage } from "./ModelsPage";
import platform from "../index";

beforeEach(() => {
  mocks.routeHref.mockReset();
  const routeHref = createRouteHref(platform.routes ?? []);
  mocks.routeHref.mockImplementation(routeHref);
  Object.assign(mocks.routeHref, { maybe: routeHref.maybe });
});

afterEach(cleanup);

describe("platform route consumers", () => {
  test("FieldsPage asks the owner for model and addon record hrefs", () => {
    render(<FieldsPage />);

    expect(mocks.routeHref).toHaveBeenCalledWith("platform.models.record", {
      id: "notes.Note",
    });
    expect(mocks.routeHref).toHaveBeenCalledWith("platform.addons.record", {
      id: "example.notes",
    });
    expect(mocks.routeHref).toHaveBeenCalledWith("platform.models.record", {
      id: "iam.User",
    });
  });

  test("ModelsPage keeps model scope search while using composed routes", () => {
    render(<ModelsPage />);

    expect(mocks.routeHref).toHaveBeenCalledWith(
      "platform.fields",
      undefined,
      { model: "notes.Note" },
    );
    expect(mocks.routeHref).toHaveBeenCalledWith("platform.models.record", {
      id: "iam.User",
    });
  });

  test("AddonsPage builds row hrefs from the addon record route", () => {
    render(<AddonsPage />);

    expect(mocks.routeHref).toHaveBeenCalledWith("platform.addons.record", {
      id: "example.notes",
    });
  });
});
