// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

vi.mock("@angee/ui", () => ({
  defineRowAction: (declaration: Record<string, unknown>) => ({
    visible: () => true,
    disabled: () => false,
    ...declaration,
  }),
  rowIdVariables: (row: { id: string }) => ({ id: row.id }),
}));

vi.mock("./i18n", () => ({
  usePartiesT: () => (key: string) => key,
}));

import {
  usePartyHandleRowActions,
  type PartyHandleActionRow,
} from "./party-handle-row-actions";

describe("party handle row actions", () => {
  test("shares ungated authored decisions while detail visibility hides decided verbs", () => {
    const review = renderHook(() => usePartyHandleRowActions("all"));
    const detail = renderHook(() => usePartyHandleRowActions("remaining"));
    const decided: PartyHandleActionRow = {
      id: "claim-1",
      is_confirmed: true,
      is_dismissed: false,
    };

    expect(review.result.current).toHaveLength(2);
    expect(review.result.current.map((action) => action.kind)).toEqual([
      "authored",
      "authored",
    ]);
    const [confirmAction, dismissAction] = review.result.current;
    const [detailConfirm, detailDismiss] = detail.result.current;
    if (confirmAction?.kind !== "authored" || dismissAction?.kind !== "authored") {
      throw new Error("Expected authored party-handle actions.");
    }
    expect(confirmAction.confirm).toBeUndefined();
    expect(dismissAction.confirm).toBeUndefined();
    expect(confirmAction.variables(decided)).toEqual({ id: "claim-1" });
    expect(confirmAction.visible(decided)).toBe(true);
    expect(detailConfirm?.visible(decided)).toBe(false);
    expect(detailDismiss?.visible(decided)).toBe(true);
  });
});
