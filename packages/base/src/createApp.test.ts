import { describe, expect, test } from "vitest";

import { parseFlatSearch, stringifyFlatSearch } from "./createApp";
import {
  dataViewReducer,
  dataViewSearchToState,
  dataViewStateToSearch,
  mergeDataViewSearch,
} from "./views/data-view-model";

describe("createApp search codec", () => {
  test("round-trips the login next parameter as a flat string", () => {
    const next = "/notes?page=2&view=board&group=status:year";

    const query = stringifyFlatSearch({ next });

    expect(query).toBe(
      "?next=%2Fnotes%3Fpage%3D2%26view%3Dboard%26group%3Dstatus%3Ayear",
    );
    expect(query).not.toContain("%22");
    expect(parseFlatSearch(query).next).toBe(next);
  });

  test("keeps primitive data-view search values unquoted", () => {
    const query = stringifyFlatSearch({
      page: 2,
      view: "board",
      group: "status:year",
      sort: "title:asc",
      empty: "",
      nil: null,
    });

    const parsed = parseFlatSearch(query);
    expect(parsed).toEqual({
      page: "2",
      view: "board",
      group: "status:year",
      sort: "title:asc",
    });
    expect(query).not.toContain("%22board%22");
  });

  test("preserves foreign search keys when data-view state changes", () => {
    const current = parseFlatSearch(
      "?tab=archive&page=2&view=board&group=status:year",
    );
    const currentState = dataViewSearchToState(current);
    const nextState = dataViewReducer(currentState, {
      type: "setSort",
      sort: { field: "title", dir: "asc" },
    });

    const query = stringifyFlatSearch(
      mergeDataViewSearch(current, dataViewStateToSearch(nextState)),
    );
    const parsed = parseFlatSearch(query);

    expect(parsed.tab).toBe("archive");
    expect(parsed.sort).toBe("title:asc");
    expect(parsed.group).toBe("status:year");
    expect(parsed.view).toBe("board");
    expect(parsed.page).toBeUndefined();
    expect(query).toContain("tab=archive");
    expect(query).not.toContain("%22");
  });
});
