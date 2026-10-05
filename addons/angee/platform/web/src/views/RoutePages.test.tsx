// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { createRouteHref } from "@angee/ui/runtime";

const mocks = vi.hoisted(() => ({
  routeHref: vi.fn(),
  scopes: {} as Record<string, string>,
}));

vi.mock("nuqs", () => ({
  parseAsString: {},
  useQueryState: (key: string) => [mocks.scopes[key] ?? null],
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
    textFilterField,
    order,
    fields,
    defaultGroup,
    groupOptions,
    baseFilter,
  }: {
    columns?: ReadonlyArray<{
      field: string;
      render?: (row: Record<string, unknown>) => React.ReactNode;
    }>;
    resource: string;
    rowHref?: (row: Record<string, unknown>) => string;
    textFilterField?: string | null;
    order?: Record<string, unknown>;
    fields?: readonly string[];
    defaultGroup?: unknown;
    groupOptions?: unknown;
    baseFilter?: unknown;
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
        : {
            id: "example.notes",
            name: "example.notes",
            label: "example.notes",
            kind: "REQUIRED",
            source: "LOCAL",
            state: "ENABLED",
          };
    return (
      <div
        data-testid={resource}
        data-row-href={rowHref?.(row) ?? ""}
        data-sort-field={columns[0]?.field}
        data-search-field={textFilterField ?? ""}
        data-order={JSON.stringify(order)}
        data-fields={JSON.stringify(fields)}
        data-default-group={JSON.stringify(defaultGroup)}
        data-group-options={JSON.stringify(groupOptions)}
        data-base-filter={JSON.stringify(baseFilter)}
      >
        {columns.map((column) => (
          <span key={column.field}>{column.render?.(row)}</span>
        ))}
      </div>
    );
  },
  useStatusTone: () => () => "neutral",
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
}));

vi.mock("./AddonCard", () => ({
  ADDON_MODEL: "platform.Addon",
  AddonCard: () => null,
  AddonCardActions: () => null,
  SOURCE_TONES: {},
  STATE_TONES: {},
}));

import { AddonsPage } from "./AddonsPage";
import { FieldsPage } from "./FieldsPage";
import { ModelsPage } from "./ModelsPage";
import platform from "../index";

beforeEach(() => {
  mocks.routeHref.mockReset();
  mocks.scopes = {};
  const routeHref = createRouteHref(platform.routes ?? []);
  mocks.routeHref.mockImplementation(routeHref);
  Object.assign(mocks.routeHref, { maybe: routeHref.maybe });
});

afterEach(cleanup);

describe("platform route consumers", () => {
  test("FieldsPage offers its server group columns and groups by model by default", () => {
    render(<FieldsPage />);
    const list = screen.getByTestId("platform.Field");
    expect(
      JSON.parse(list.getAttribute("data-group-options")!).map((option: { group: { field: string } }) => option.group.field),
    ).toEqual(["addon", "model", "kind", "relation_target"]);
    expect(JSON.parse(list.getAttribute("data-default-group")!)).toEqual({ field: "model" });
  });

  test("FieldsPage scoped to one model has no default group", () => {
    mocks.scopes = { model: "notes.Note" };
    render(<FieldsPage />);
    const list = screen.getByTestId("platform.Field");
    // An explicit null, not an omitted default.
    expect(list.getAttribute("data-default-group")).toBe("null");
    expect(list.getAttribute("data-group-options")).not.toBeNull();
  });

  test("FieldsPage scoped to one addon still groups by model", () => {
    mocks.scopes = { addon: "example.notes" };
    render(<FieldsPage />);
    expect(JSON.parse(screen.getByTestId("platform.Field").getAttribute("data-default-group")!)).toEqual({
      field: "model",
    });
  });

  test("FieldsPage keeps model and addon scopes as exact server filters", () => {
    mocks.scopes = { model: "notes.Note", addon: "example.notes" };
    render(<FieldsPage />);
    expect(JSON.parse(screen.getByTestId("platform.Field").getAttribute("data-base-filter")!)).toEqual({
      model: { exact: "notes.Note" }, addon: { exact: "example.notes" },
    });
  });

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

  test("AddonsPage displays server labels and searches and sorts by canonical name", () => {
    render(<AddonsPage />);

    const list = screen.getByTestId("platform.Addon");
    expect(screen.getAllByText("example.notes")).toHaveLength(2);
    expect(list.getAttribute("data-sort-field")).toBe("name");
    expect(list.getAttribute("data-search-field")).toBe("name");
    expect(list.getAttribute("data-order")).toBe(JSON.stringify({ name: "ASC" }));
    expect(list.getAttribute("data-fields")).toContain('"label"');
  });
});
