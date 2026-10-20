import type { Meta, StoryObj } from "@storybook/react-vite";
import { baseIcons } from "@angee/ui";
import { GlyphSheet } from "./design-system/token-sheets";
const meta = { title: "Foundations/Glyphs", component: GlyphSheet, parameters: { layout: "fullscreen", owner: baseIcons } } satisfies Meta<typeof GlyphSheet>;
export default meta;
export const Default: StoryObj<typeof meta> = {};
