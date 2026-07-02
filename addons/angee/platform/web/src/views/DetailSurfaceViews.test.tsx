// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const routerMocks = vi.hoisted(() => ({
  params: {} as Record<string, string>,
}));

const platformMocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  usePlatformAddon: vi.fn(),
  usePlatformModel: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  useParams: () => routerMocks.params,
}));

vi.mock("@angee/ui", async () => ({
  ...(await vi.importActual<typeof import("@angee/ui")>("@angee/ui")),
  useRouteRecordId: () => routerMocks.params.id,
}));

vi.mock("../i18n", () => ({
  usePlatformT: () => (key: string, vars?: Record<string, number>) =>
    vars?.count ? `${key}:${vars.count}` : key,
}));

vi.mock("../lib/cells", () => ({
  LinkedChips: ({
    items,
    href,
    format,
  }: {
    items: readonly string[];
    href: (item: string) => string;
    format?: (item: string) => string;
  }) => (
    <span>
      {items.length
        ? items.map((item) => (
            <a key={item} href={href(item)}>
              {format ? format(item) : item}
            </a>
          ))
        : "—"}
    </span>
  ),
  RouterLink: ({
    href,
    children,
  }: {
    href: string;
    children: React.ReactNode;
  }) => <a href={href}>{children}</a>,
  useRouteNavigate: () => platformMocks.navigate,
}));

vi.mock("../lib/explorer", () => ({
  usePlatformAddon: platformMocks.usePlatformAddon,
  usePlatformModel: platformMocks.usePlatformModel,
}));

import { AddonDetail } from "./AddonDetail";
import { ModelDetail } from "./ModelDetail";

beforeEach(() => {
  routerMocks.params = {};
  platformMocks.navigate.mockClear();
  platformMocks.usePlatformAddon.mockReset();
  platformMocks.usePlatformModel.mockReset();
});

afterEach(() => cleanup());

describe("platform detail surfaces", () => {
  test("AddonDetail preserves loading and not-found states", () => {
    platformMocks.usePlatformAddon.mockReturnValue({
      addon: undefined,
      dependedBy: [],
      dependsOn: [],
      fetching: true,
      modelLabels: [],
    });

    const view = render(<AddonDetail />);

    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText("detail.addon.loading")).toBeTruthy();

    platformMocks.usePlatformAddon.mockReturnValue({
      addon: undefined,
      dependedBy: [],
      dependsOn: [],
      fetching: false,
      modelLabels: [],
    });
    routerMocks.params = { id: "angee.missing" };
    view.rerender(<AddonDetail />);

    expect(screen.getByRole("heading", {
      name: "detail.addon.notFound",
    })).toBeTruthy();
    expect(screen.getByText("angee.missing")).toBeTruthy();
  });

  test("AddonDetail renders metrics and dependency sections through the shared surface", () => {
    routerMocks.params = { id: "angee.storage" };
    platformMocks.usePlatformAddon.mockReturnValue({
      addon: {
        field_count: 7,
        id: "angee.storage",
        kind: "required",
        label: "Storage",
        model_count: 2,
        namespace: "angee",
        resource_count: 1,
      },
      dependedBy: ["angee.operator"],
      dependsOn: ["angee.iam"],
      fetching: false,
      modelLabels: ["storage.File"],
    });

    render(<AddonDetail />);

    expect(screen.getByRole("heading", { name: "Storage" })).toBeTruthy();
    expect(screen.getByText("angee.storage")).toBeTruthy();
    expect(screen.getByText("col.models")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText("detail.dependencies")).toBeTruthy();
    expect((screen.getByRole("link", { name: "iam" }) as HTMLAnchorElement).pathname)
      .toBe("/platform/addons/angee.iam");
    expect(screen.getByText("detail.modelsWithCount:1")).toBeTruthy();
    expect(
      (screen.getByRole("link", { name: "storage.File" }) as HTMLAnchorElement)
        .pathname,
    ).toBe("/platform/models/storage.File");
  });

  test("ModelDetail keeps metric links navigable", () => {
    platformMocks.usePlatformModel.mockReturnValue({
      dependedBy: ["notes.Attachment"],
      fetching: false,
      model: {
        addon_id: "example.notes",
        addon_label: "Notes",
        app_label: "notes",
        db_table: "notes_note",
        depends_on: ["iam.User"],
        field_count: 8,
        label: "notes.Note",
        model_name: "Note",
        relation_count: 2,
        resource_type: "notes.note",
      },
    });

    render(<ModelDetail />);

    expect(screen.getByRole("heading", { name: "Note" })).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: /col.graph/ }));
    expect(platformMocks.navigate).toHaveBeenCalledWith(
      "/platform?model=notes.Note",
    );
    expect(screen.getByText("detail.definition")).toBeTruthy();
    expect(screen.getByText("notes_note")).toBeTruthy();
  });
});
