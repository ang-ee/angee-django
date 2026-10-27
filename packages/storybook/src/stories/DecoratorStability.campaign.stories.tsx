import { useRef, useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { useArgs, useGlobals } from "storybook/preview-api";
import { expect, userEvent, waitFor, within } from "storybook/test";
import { useRouter } from "@tanstack/react-router";
import { Button, Input, useAppearance } from "@angee/ui";

import preview from "../../.storybook/preview";
import { previewThemeId, themeGlobalValue } from "../theme-global";
import { RoutedRuntimeFixture, jsonResponse, storyAppearance, storySchema } from "./runtime-fixtures";

function DraftProbe({ label }: { label: string }) {
  const [draft, setDraft] = useState("");
  const [count, setCount] = useState(0);
  const router = useRouter();
  const appearance = useAppearance();
  const initialRouter = useRef(router);
  return <>
    <Input aria-label="Draft" value={draft} onChange={(event) => setDraft(event.target.value)} />
    <Button onClick={() => setCount((value) => value + 1)}>{label}: {count}</Button>
    <output aria-label="Effective appearance">{appearance.effectiveThemeId} / {appearance.colorScheme}</output>
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
        themeId: themeGlobalValue(previewThemeId(globals.themeId) === "angee.stock" ? "angee.angee" : "angee.stock"),
        colorScheme: globals.colorScheme === "dark" ? "light" : "dark",
      })}>Change appearance</Button>
      <output aria-label="Preview appearance">{previewThemeId(globals.themeId)} / {String(globals.colorScheme)}</output>
    </div>;
  },
} satisfies Meta<{ label: string }>;

export default meta;

export const PreservesState: StoryObj<typeof meta> = {
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    const draft = await canvas.findByRole("textbox", { name: "Draft" });
    await userEvent.type(draft, "Keep this draft");
    await userEvent.click(canvas.getByRole("button", { name: `${args.label}: 0` }));
    await userEvent.click(canvas.getByRole("button", { name: "Change label" }));
    await canvas.findByRole("button", { name: "Updated: 1" });
    const [theme, scheme] = (canvas.getByLabelText("Preview appearance").textContent ?? "").split(" / ");
    const nextAppearance = `${theme === "angee.stock" ? "angee.angee" : "angee.stock"} / ${scheme === "dark" ? "light" : "dark"}`;
    await userEvent.click(canvas.getByRole("button", { name: "Change appearance" }));
    await waitFor(() => expect(canvas.getByLabelText("Preview appearance").textContent).toBe(nextAppearance));
    await waitFor(() => expect(canvas.getByLabelText("Effective appearance").textContent).toBe(nextAppearance));
    await expect(canvas.getByRole("textbox", { name: "Draft" })).toHaveValue("Keep this draft");
    await expect(canvas.getByRole("button", { name: "Updated: 1" })).toBeVisible();
    await expect(canvas.getByLabelText("Router identity").textContent).toBe("Preserved");

    const values = preview.globalTypes?.themeId?.toolbar?.items?.map(
      (item) => typeof item === "string" ? item : item.value,
    ) ?? [];
    await expect(values).toContain(themeGlobalValue("angee.stock"));
    await expect(values).not.toContain("angee_2Ebrand");
    await expect(new Set(values).size).toBe(values.length);
  },
};

const schemas = storySchema(async () => jsonResponse({ data: {} }));

/** The same environment owner supplied through native preview parameters. */
export const DataFixturePreservesState: StoryObj<typeof meta> = {
  parameters: { angeeFixture: { schemas } },
  play: PreservesState.play,
};

/** Direct routed fixtures retain the same toolbar and local-state contract. */
export const RoutedFixturePreservesState: StoryObj<typeof meta> = {
  parameters: { angeeOwnRuntime: true },
  render: (args, { globals }) => <RoutedRuntimeFixture
    schemas={schemas}
    collectionPath="/records"
    appearance={storyAppearance(globals)}
  >{meta.render(args)}</RoutedRuntimeFixture>,
  play: PreservesState.play,
};
