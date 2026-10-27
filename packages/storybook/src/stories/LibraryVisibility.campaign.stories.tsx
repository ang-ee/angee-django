import type { Meta, StoryObj } from "@storybook/react-vite";
import { expect, waitFor } from "storybook/test";

import library from "./Library.stories";

const meta = {
  title: "Foundations/Library lifetime campaign",
  component: library.component,
  parameters: library.parameters,
} satisfies Meta<typeof library.component>;
export default meta;

export const ReleasesAndRevisits: StoryObj<typeof meta> = {
  play: async ({ canvasElement }) => {
    const card = (name: string) => {
      const element = canvasElement.querySelector<HTMLElement>(`[data-catalog-export="@angee/ui:${name}"]`);
      if (!element) throw new Error(`Missing ${name} catalog card.`);
      return element;
    };
    const visit = async (name: string) => {
      card(name).scrollIntoView({ block: "center" });
      await waitFor(() => expect(card(name).querySelector("iframe")).not.toBeNull());
      await waitFor(() => {
        for (const frame of canvasElement.querySelectorAll("iframe")) {
          const rect = frame.getBoundingClientRect();
          expect(rect.bottom).toBeGreaterThanOrEqual(-200);
          expect(rect.top).toBeLessThanOrEqual(window.innerHeight + 200);
        }
      });
    };
    await visit("Button");
    const firstFrame = card("Button").querySelector("iframe");
    const height = document.documentElement.scrollHeight;
    for (const name of ["ResourceCreate", "Form", "Select"]) {
      await visit(name);
      await expect(document.documentElement.scrollHeight).toBe(height);
      await expect(firstFrame?.isConnected).toBe(false);
    }
    await visit("Button");
    await expect(card("Button").querySelector("iframe")).not.toBe(firstFrame);
    await expect(document.documentElement.scrollHeight).toBe(height);
  },
};
