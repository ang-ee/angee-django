import { useRef, useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { useArgs, useGlobals } from "storybook/preview-api";
import { expect, userEvent, waitFor, within } from "storybook/test";
import { useRouter } from "@tanstack/react-router";
import { Button, Input } from "@angee/ui";

import preview from "../../.storybook/preview";

function DraftProbe({ label }: { label: string }) {
  const [draft, setDraft] = useState("");
  const [count, setCount] = useState(0);
  const router = useRouter();
  const initialRouter = useRef(router);
  return <>
    <Input aria-label="Draft" value={draft} onChange={(event) => setDraft(event.target.value)} />
    <Button onClick={() => setCount((value) => value + 1)}>{label}: {count}</Button>
    <output aria-label="Router identity">{router === initialRouter.current ? "Preserved" : "Replaced"}</output>
  </>;
}

const meta = {
  title: "Foundations/Decorator stability campaign",
  args: { label: "Count" },
  render: function Render(args: { label: string }) {
    const [, updateArgs] = useArgs();
    const [globals, updateGlobals] = useGlobals();
    return <div className="grid max-w-sm gap-3">
      <DraftProbe label={args.label} />
      <Button onClick={() => updateArgs({ label: "Updated" })}>Change label</Button>
      <Button onClick={() => updateGlobals({
        themeId: globals.themeId === "angee.stock" ? "angee.angee" : "angee.stock",
        colorScheme: globals.colorScheme === "dark" ? "light" : "dark",
      })}>Change appearance</Button>
      <output aria-label="Preview appearance">{String(globals.themeId)} / {String(globals.colorScheme)}</output>
    </div>;
  },
} satisfies Meta<{ label: string }>;

export default meta;

export const PreservesState: StoryObj<typeof meta> = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const draft = await canvas.findByRole("textbox", { name: "Draft" });
    await userEvent.type(draft, "Keep this draft");
    await userEvent.click(canvas.getByRole("button", { name: "Count: 0" }));
    await userEvent.click(canvas.getByRole("button", { name: "Change label" }));
    await canvas.findByRole("button", { name: "Updated: 1" });
    const [theme, scheme] = (canvas.getByLabelText("Preview appearance").textContent ?? "").split(" / ");
    const nextAppearance = `${theme === "angee.stock" ? "angee.angee" : "angee.stock"} / ${scheme === "dark" ? "light" : "dark"}`;
    await userEvent.click(canvas.getByRole("button", { name: "Change appearance" }));
    await waitFor(() => expect(canvas.getByLabelText("Preview appearance").textContent).toBe(nextAppearance));
    await expect(canvas.getByRole("textbox", { name: "Draft" })).toHaveValue("Keep this draft");
    await expect(canvas.getByRole("button", { name: "Updated: 1" })).toBeVisible();
    await expect(canvas.getByLabelText("Router identity").textContent).toBe("Preserved");

    const values = preview.globalTypes?.themeId?.toolbar?.items?.map(
      (item: string | { value: unknown }) => typeof item === "string" ? item : item.value,
    ) ?? [];
    await expect(values).toContain("angee.stock");
    await expect(values).not.toContain("angee.brand");
    await expect(new Set(values).size).toBe(values.length);
  },
};
