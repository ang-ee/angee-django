// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { AppRuntimeProvider, ToastProvider, createRouteHref } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const reads = vi.hoisted(() => ({
  calls: [] as { document: unknown; variables: unknown }[],
  data: new Map<unknown, (variables: Record<string, unknown>) => unknown>(),
}));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: (document: unknown, variables: Record<string, unknown> | undefined) => {
    reads.calls.push({ document, variables });
    return { data: reads.data.get(document)?.(variables ?? {}), isFetching: false, error: null };
  },
}));

import { CertainSameAsPairs, PartyReviewCounts, SameAsPairs } from "./documents";
import { enPartiesMessages } from "./i18n";
import parties from "./index";
import { ReviewPage } from "./ReviewPage";

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://parties-review" });

const party = (id: string) => ({ id, display_name: `Zoe ${id}`, display_avatar: null });
/** Pairs numbered from ``from``: their ids continue in review order. */
const pairs = (from: number, count: number) => Array.from({ length: count }, (_value, index) => {
  const number = from + index;
  return { id: `pair-${number}`, normalized_value: `zoe${number}@example.test`, left: party(`l${number}`), right: party(`r${number}`) };
});
const afterOf = (variables: Record<string, unknown>) => (variables.after as string | null | undefined) ?? null;
const readsOf = (document: unknown) => reads.calls.filter((call) => call.document === document).map((call) => call.variables);

beforeEach(() => {
  reads.calls = [];
  reads.data.clear();
  reads.data.set(PartyReviewCounts, () => ({
    party_statements_aggregate: { aggregate: { count: 3 } },
    same_as_pair_counts: { pairs: 120, certain: 180 },
  }));
  reads.data.set(SameAsPairs, (variables) => ({
    same_as_pairs: afterOf(variables) === null ? pairs(1, 50) : afterOf(variables) === "pair-50" ? pairs(51, 50) : pairs(101, 20),
  }));
  reads.data.set(CertainSameAsPairs, (variables) => ({
    certain_same_as_pairs: (afterOf(variables) === null ? pairs(1, 50) : pairs(51, 50)).map((pair) => ({
      ...pair, survivor: { id: pair.left.id },
    })),
  }));
});
afterEach(() => {
  cleanup();
  clearClients();
});

function renderReview() {
  const root = createRootRoute({ component: ReviewPage });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(
    <Provider>
      <AppRuntimeProvider runtime={{ routeHref: createRouteHref(parties.routes ?? []) }}>
        <ToastProvider><RouterProvider router={router} /></ToastProvider>
      </AppRuntimeProvider>
    </Provider>,
  );
}

describe("the Review page's possible duplicates", () => {
  test("page after the last pair seen, and back", async () => {
    renderReview();

    expect(await screen.findByText("zoe50@example.test")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Next page" }).at(-1)!);
    await waitFor(() => expect(readsOf(SameAsPairs)).toContainEqual({ limit: 50, after: "pair-50" }));
    expect(await screen.findByText("zoe100@example.test")).toBeTruthy();

    fireEvent.click(screen.getAllByRole("button", { name: "Previous page" }).at(-1)!);
    expect(await screen.findByText("zoe1@example.test")).toBeTruthy();
  });

  test("Merge certain pairs says which batch of how many, and the next batch continues after it", async () => {
    renderReview();
    const label = enPartiesMessages["review.sameAs.mergeCertain"]!.replace("{count}", "50").replace("{total}", "180");

    fireEvent.click(await screen.findByRole("button", { name: label }));

    const note = enPartiesMessages["duplicates.nextBatch"]!.replace("{count}", "50").replace("{total}", "180");
    expect(await screen.findByText(note)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: enPartiesMessages["duplicates.cancel"]! }));
    await waitFor(() => expect(readsOf(CertainSameAsPairs)).toContainEqual({ limit: 50, after: "pair-50" }));
  });
});
