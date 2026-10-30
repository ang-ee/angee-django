// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import type * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { ListColumn, ListViewProps } from "@angee/ui";

import { PersonRelationshipsTab } from "./PersonRelationships";

const capture = vi.hoisted(() => ({ props: [] as ListViewProps[] }));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  ListView: (props: ListViewProps) => {
    capture.props.push(props);
    return null;
  },
}));

vi.mock("./i18n", () => ({
  usePartiesT: () => (key: string) => key,
}));

const outgoing = {
  id: "rel_1", party: { id: "party_7", display_name: "Maya" },
  kind: { name: "Mother", inverse_name: "Child" }, other_party: null, other_name: "Jane",
};
const incoming = {
  id: "rel_2", party: { id: "party_9", display_name: "Bob" },
  kind: { name: "Mother", inverse_name: "Child" }, other_party: { display_name: "Maya" }, other_name: "",
};
const symmetric = {
  id: "rel_3", party: { id: "party_8", display_name: "Ann" },
  kind: { name: "Friend", inverse_name: "" }, other_party: { display_name: "Maya" }, other_name: "",
};

function cell(column: ListColumn | undefined, row: Record<string, unknown>): React.ReactNode {
  return column?.render?.(row as never);
}

describe("PersonRelationshipsTab", () => {
  beforeEach(() => { capture.props = []; });

  test("lists both directions of the person's edges as one compact collection", () => {
    render(<PersonRelationshipsTab recordId="party_7" />);

    expect(capture.props).toHaveLength(1);
    const props = capture.props[0]!;
    expect(props).toMatchObject({
      resource: "parties.Relationship",
      presentation: "embedded",
      baseFilter: { OR: [{ party: { exact: "party_7" } }, { other_party: { exact: "party_7" } }] },
      emptyContent: "person.empty.relationships",
    });
    expect(props.fields).toEqual(expect.arrayContaining(["party.id", "kind.inverse_name", "other_name"]));
    expect(props.columns.map((column) => column.field)).toEqual([
      "party.id", "kind.name", "other_party.display_name", "started_at", "ended_at",
    ]);
  });

  test("reads each row from this person's side, with a direction column", () => {
    render(<PersonRelationshipsTab recordId="party_7" />);
    const [direction, kind, person] = capture.props[0]!.columns;

    expect(cell(direction, outgoing)).toBe("relationship.direction.outgoing");
    expect(cell(kind, outgoing)).toBe("Mother");
    expect(cell(person, outgoing)).toBe("Jane");

    expect(cell(direction, incoming)).toBe("relationship.direction.incoming");
    expect(cell(kind, incoming)).toBe("Child");
    expect(cell(person, incoming)).toBe("Bob");

    expect(cell(kind, symmetric)).toBe("Friend");
    expect(cell(person, symmetric)).toBe("Ann");
  });
});
