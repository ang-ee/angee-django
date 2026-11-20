// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { AppRuntimeProvider, ToastProvider, createRouteHref, formatNumber } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const reads = vi.hoisted(() => ({
  calls: [] as { document: unknown; variables: unknown; enabled: boolean }[],
  options: [] as { document: unknown; enabled: boolean }[],
  data: new Map<unknown, (variables: Record<string, unknown>) => unknown>(),
}));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: (document: unknown, variables: Record<string, unknown> | undefined, options?: { enabled?: boolean }) => {
    reads.calls.push({ document, variables, enabled: options?.enabled ?? true });
    reads.options.push({ document, enabled: options?.enabled ?? true });
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
/** The reads a document's hook sends: a disabled query sends none. */
const readsOf = (document: unknown) =>
  reads.calls.filter((call) => call.document === document && call.enabled).map((call) => call.variables);

beforeEach(() => {
  reads.calls = [];
  reads.options = [];
  reads.data.clear();
  reads.data.set(PartyReviewCounts, () => ({
    party_statements_aggregate: { aggregate: { count: 3 } },
    same_as_pair_counts: { pairs: 120, certain: 180 },
  }));
  reads.data.set(SameAsPairs, (variables) => ({
    same_as_pairs: afterOf(variables) === null ? pairs(1, 50) : afterOf(variables) === "pair-50" ? pairs(51, 50) : pairs(101, 20),
  }));
  // 180 certain pairs, fifty a page after the last pair of the page before.
  reads.data.set(CertainSameAsPairs, (variables) => {
    const after = afterOf(variables);
    const from = after === null ? 1 : Number(after.slice("pair-".length)) + 1;
    return {
      certain_same_as_pairs: pairs(from, Math.max(0, Math.min(50, 181 - from))).map((pair) => ({
        ...pair, survivor: { id: pair.left.id },
      })),
    };
  });
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

  test("Merge all certain pairs says how many and lists every one of them, page after page", async () => {
    renderReview();
    const label = enPartiesMessages["review.sameAs.mergeCertain"]!.replace("{total}", "180");

    fireEvent.click(await screen.findByRole("button", { name: label }));

    await waitFor(() => expect(readsOf(CertainSameAsPairs)).toEqual(expect.arrayContaining([
      { limit: 50, after: null }, { limit: 50, after: "pair-50" }, { limit: 50, after: "pair-100" },
      { limit: 50, after: "pair-150" },
    ])));
    // The last page held thirty: nothing is read past it.
    expect(readsOf(CertainSameAsPairs)).not.toContainEqual({ limit: 50, after: "pair-180" });
  });

  test("past the server's cap the totals read as a lower bound", async () => {
    reads.data.set(PartyReviewCounts, () => ({
      party_statements_aggregate: { aggregate: { count: 3 } },
      same_as_pair_counts: { pairs: 1000, certain: 180, capped: true },
    }));
    renderReview();
    const bound = (value: number) => enPartiesMessages["review.cappedCount"]!.replace("{count}", formatNumber(value));
    const label = enPartiesMessages["review.sameAs.mergeCertain"]!.replace("{total}", bound(180));

    expect(await screen.findByRole("button", { name: label })).toBeTruthy();
    expect(screen.getByText(bound(1000))).toBeTruthy();
  });

  test("while the batch dialog is open the page's own reads are held, and read again once it closes", async () => {
    renderReview();
    const label = enPartiesMessages["review.sameAs.mergeCertain"]!.replace("{total}", "180");
    fireEvent.click(await screen.findByRole("button", { name: label }));
    await screen.findByText(enPartiesMessages["duplicates.title"]!);

    const page = [PartyReviewCounts, SameAsPairs];
    const whileOpen = reads.options.filter((call) => page.includes(call.document as never)).slice(-2);
    expect(whileOpen.map((call) => call.enabled)).toEqual([false, false]);

    fireEvent.click(screen.getByRole("button", { name: enPartiesMessages["duplicates.cancel"]! }));
    await waitFor(() => {
      const after = reads.options.filter((call) => page.includes(call.document as never)).slice(-2);
      expect(after.map((call) => call.enabled)).toEqual([true, true]);
    });
  });
});

