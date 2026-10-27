import type { Meta, StoryObj } from "@storybook/react-vite";
import { useRouter, useRouterState } from "@tanstack/react-router";
import { expect, userEvent, waitFor, within } from "storybook/test";
import { Button, TopMenuTabs } from "@angee/ui";

import { RuntimeFixture, RoutedRuntimeFixture, jsonResponse, storyAppearance, storySchema } from "./runtime-fixtures";

const schemas = storySchema(async () => jsonResponse({ data: {} }));
const tabs = [{ id: "list", label: "List" }, { id: "board", label: "Board" }];

function TabHistoryProbe() {
  const router = useRouter();
  const href = useRouterState({ select: (state) => state.location.href });
  return <div className="grid gap-3">
    <TopMenuTabs tabs={tabs} />
    <output aria-label="Fixture URL">{href}</output>
    <Button onClick={() => void router.navigate({ to: "/records", search: { tab: "board", keep: "outside" } })}>Visit board location</Button>
    <Button onClick={() => router.history.back()}>History back</Button>
    <Button onClick={() => router.history.forward()}>History forward</Button>
  </div>;
}

const meta = {
  title: "Foundations/Runtime fixture campaign",
  parameters: { angeeOwnRuntime: true },
} satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;

export const RoutedQueryHistory: Story = {
  render: (_args, { globals }) => <RoutedRuntimeFixture
    schemas={schemas}
    collectionPath="/records"
    initialEntry="/records?tab=board&keep=outside"
    appearance={storyAppearance(globals)}
  ><TabHistoryProbe /></RoutedRuntimeFixture>,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const list = await canvas.findByRole("tab", { name: "List" });
    const board = canvas.getByRole("tab", { name: "Board" });
    const url = () => new URL(canvas.getByLabelText("Fixture URL").textContent ?? "", "https://example.test");
    await expect(board).toHaveAttribute("aria-selected", "true");
    await userEvent.click(list);
    await waitFor(() => expect(url().searchParams.get("tab")).toBe("list"));
    await expect(list).toHaveAttribute("aria-selected", "true");
    await expect(url().searchParams.get("keep")).toBe("outside");
    // TopMenuTabs uses Nuqs's default replace. A native navigation adds the next
    // entry, so back/forward must restore both the router URL and the tab owner.
    await userEvent.click(canvas.getByRole("button", { name: "Visit board location" }));
    await waitFor(() => expect(board).toHaveAttribute("aria-selected", "true"));
    await userEvent.click(canvas.getByRole("button", { name: "History back" }));
    await waitFor(() => expect(list).toHaveAttribute("aria-selected", "true"));
    await expect(url().searchParams.get("tab")).toBe("list");
    await expect(url().searchParams.get("keep")).toBe("outside");
    await userEvent.click(canvas.getByRole("button", { name: "History forward" }));
    await waitFor(() => expect(board).toHaveAttribute("aria-selected", "true"));
    await expect(url().searchParams.get("tab")).toBe("board");
    await expect(url().searchParams.get("keep")).toBe("outside");
  },
};

export const RouterlessQueryState: Story = {
  render: (_args, { globals }) => <RuntimeFixture schemas={schemas} appearance={storyAppearance(globals)}>
    <TopMenuTabs tabs={tabs} />
  </RuntimeFixture>,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByRole("tab", { name: "List" })).toHaveAttribute("aria-selected", "true");
    const board = canvas.getByRole("tab", { name: "Board" });
    await userEvent.click(board);
    await waitFor(() => expect(board).toHaveAttribute("aria-selected", "true"));
  },
};
