import { describe, expect, test, vi } from "vitest";

import type { ResourceViewContextValue } from "../resource-view-context";
import { listResultFromPageState } from "./table-state";

describe("listResultFromPageState", () => {
  const resourceView = {
    state: { pagination: { pageIndex: 0, pageSize: 25 } },
    setPage: vi.fn(),
  } as unknown as ResourceViewContextValue;
  const page = { resourceView, error: null, fetching: false, refetch: vi.fn(), rows: [], total: 3 };

  test("a page counts records unless the paging surface says it counts groups", () => {
    expect(listResultFromPageState(page).pageUnit).toBe("records");
    expect(listResultFromPageState({ ...page, pageUnit: "groups" }).pageUnit).toBe("groups");
  });
});
