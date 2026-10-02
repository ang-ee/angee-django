import { describe, expect, test } from "vitest";

import type {
  ComparisonAnswer,
  ComparisonProposal,
  ComparisonTopic,
} from "./comparison-data";
import {
  comparisonCellValue,
  comparisonRows,
  isEmptyComparisonValue,
  proposalColumnLabel,
} from "./comparison-model";

const topics: ComparisonTopic[] = [
  { id: "top_b", key: "b", name: "B", sort_order: 20 },
  { id: "top_z", key: "z", name: "Z", sort_order: 10 },
  { id: "top_a", key: "a", name: "A", sort_order: 10 },
];
const proposal: ComparisonProposal = {
  id: "prp_1",
  party: { display_name: "Northstar" },
  responder: { id: "user_1", display_name: "Alice" },
  cost: null,
};
const answers: ComparisonAnswer[] = [
  {
    id: "ans_1",
    proposal: { id: "prp_1" },
    topic: { id: "top_a" },
    body: "Aligned answer",
  },
];

describe("proposal comparison model", () => {
  test("orders topics and includes facts only when requested", () => {
    expect(comparisonRows(topics).map((row) => row.id)).toEqual([
      "topic:top_a",
      "topic:top_z",
      "topic:top_b",
    ]);
    expect(comparisonRows(topics, ["statement", "cost"]).map((row) => row.id)).toEqual([
      "topic:top_a", "topic:top_z", "topic:top_b", "fact:statement", "fact:cost",
    ]);
  });

  test("aligns an answer by proposal and topic ids", () => {
    const row = comparisonRows(topics)[0];
    expect(row && comparisonCellValue(row, proposal, answers)).toBe(
      "Aligned answer",
    );
  });

  test("keeps redacted null and empty text on the same empty branch", () => {
    expect(isEmptyComparisonValue(null)).toBe(true);
    expect(isEmptyComparisonValue("  ")).toBe(true);
    expect(isEmptyComparisonValue("0")).toBe(false);
  });

  test("labels responders by their person name with a party and neutral fallback", () => {
    expect(proposalColumnLabel(proposal)).toBe("Alice");
    expect(
      proposalColumnLabel({
        id: "prp_2",
        responder: { id: "user_2", display_name: "Alice" },
      }),
    ).toBe(
      "Alice",
    );
    expect(proposalColumnLabel({ id: "prp_3" }, "Responder")).toBe("Responder");
  });
});
