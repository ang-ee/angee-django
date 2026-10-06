// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { ListViewProps, RecordPanelContext } from "@angee/ui";

import { IdentityTab } from "./IdentityTab";

const capture = vi.hoisted(() => ({ props: null as ListViewProps | null }));
const actions = [{ id: "confirm" }, { id: "dismiss" }];

vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  ListView: (props: ListViewProps) => {
    capture.props = props;
    return null;
  },
  useRouteSearch: () => ({}),
  useResourceRecordHrefLookup: () => () => undefined,
}));

vi.mock("./party-handle-row-actions", () => ({
  usePartyHandleRowActions: () => actions,
}));

const context = (formReadOnly: boolean) => ({
  recordId: "party_7",
  form: { formReadOnly } as RecordPanelContext["form"],
  reload: vi.fn(),
  focusField: vi.fn(),
}) satisfies RecordPanelContext;

describe("IdentityTab", () => {
  beforeEach(() => { capture.props = null; });

  test("keeps identity decisions available on an editable Party form", () => {
    render(<IdentityTab {...context(false)} />);
    expect(capture.props?.presentation).toBe("embedded");
    expect(capture.props?.rowActions).toBe(actions);
  });

  test("does not expose identity mutations in a read-only record peek", () => {
    render(<IdentityTab {...context(true)} />);
    expect(capture.props?.rowActions).toEqual([]);
  });
});
