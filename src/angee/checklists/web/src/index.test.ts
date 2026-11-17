import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import checklists, { checklistFromRow, checklistTones } from "./index";

describe("angee.checklists addon manifest", () => {
  test("satisfies the rendered-addon invariants and registers the status tones", () => {
    expect(() => expectValidBaseAddon(checklists)).not.toThrow();
    expect(checklists.statusTones).toEqual(checklistTones);
  });

  test("reads a row's checklist only when its whole shape is present", () => {
    const checklist = {
      status: "risk", detail: "Signature smudged.", complete: false, stale: false, recorded_at: null,
      groups: [{ key: "identity", label: "Identity", status: "risk", detail: "Signature smudged.", item_keys: ["owner"] }],
      items: [{ key: "owner", label: "Owner known", category: "identity", blocking: true, status: "risk",
        detail: "Signature smudged.", stale: false, recorded_at: null, evidence: [{ model: "inspections.Report", id: "rpt_1" }] }],
    };
    expect(checklistFromRow({ id: "1", checklist })).toEqual(checklist);
    expect(checklistFromRow({ id: "1", checklist: { ...checklist, items: [{ key: "owner" }] } })).toBeNull();
    expect(checklistFromRow({ id: "1" })).toBeNull();
  });
});
