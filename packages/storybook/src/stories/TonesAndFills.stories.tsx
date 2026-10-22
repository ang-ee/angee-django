import type { Meta, StoryObj } from "@storybook/react-vite";
import { FILLS, TONES } from "@angee/ui";
import { TonesSheet } from "./design-system/token-sheets";
const meta = { title: "Foundations/Tones and fills", component: TonesSheet, parameters: { layout: "fullscreen", owner: [TONES, FILLS] } } satisfies Meta<typeof TonesSheet>;
export default meta;
export const Default: StoryObj<typeof meta> = {};
