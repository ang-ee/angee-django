// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { pageChildren, pageElementProps, parsePageColumns, type DrawerResourceListProps, type ListProps } from "@angee/ui";

import { ProposalReviewsPanel } from "./ProposalsPage";

const mocks = vi.hoisted(() => ({
  lists: [] as DrawerResourceListProps[],
  user: { id: "usr_1" } as { id: string } | null,
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  DrawerResourceList: (props: DrawerResourceListProps) => {
    mocks.lists.push(props);
    return null;
  },
  useRuntimeAuth: () => ({ user: mocks.user }),
}));

vi.mock("../i18n", () => ({
  useProposalsT: () => (key: string) => key,
}));

function listDeclaration(props: DrawerResourceListProps | undefined): ListProps | undefined {
  return pageChildren(props?.children)
    .map((child) => pageElementProps<ListProps>(child, "list"))
    .find((candidate): candidate is ListProps => Boolean(candidate));
}

describe("ProposalReviewsPanel", () => {
  beforeEach(() => {
    mocks.lists = [];
    mocks.user = { id: "usr_1" };
  });

  test("lists every readable review once, with a Mine shortcut over the same collection", () => {
    render(<ProposalReviewsPanel recordId="prp_1" />);

    expect(mocks.lists).toHaveLength(1);
    const props = mocks.lists[0];
    expect(props).toMatchObject({
      resource: "proposals.Review",
      presentation: "embedded",
      baseFilter: { proposal: { exact: "prp_1" } },
      createDefaults: { proposal: "prp_1", reviewer: "usr_1" },
      hideCreate: false,
    });
    const list = listDeclaration(props);
    expect(list).toMatchObject({
      filterOptions: [{ id: "mine", label: "proposal.reviews.mine", filter: { reviewer: { exact: "usr_1" } } }],
      search: { shortcuts: [{ kind: "toggle", id: "mine" }] },
      chrome: { search: true },
    });
    expect(parsePageColumns(list?.children).map((column) => column.field)).toEqual(["reviewer", "body", "updated_at"]);
  });

  test("a signed-out viewer reads the reviews without create or Mine", () => {
    mocks.user = null;
    render(<ProposalReviewsPanel recordId="prp_1" />);

    const props = mocks.lists[0];
    expect(props).toMatchObject({ hideCreate: true, createDefaults: undefined });
    const list = listDeclaration(props);
    expect(list?.filterOptions).toEqual([]);
    expect(list?.search).toBeUndefined();
    expect(list?.chrome).toBeUndefined();
  });
});
