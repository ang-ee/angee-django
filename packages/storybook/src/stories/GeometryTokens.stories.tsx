import type { Meta, StoryObj } from "@storybook/react-vite";
import { GeometrySheet } from "./design-system/token-sheets";
const meta = { title: "Foundations/Radius, spacing and control heights", component: GeometrySheet, parameters: { layout: "fullscreen" } } satisfies Meta<typeof GeometrySheet>;
export default meta;
export const Default: StoryObj<typeof meta> = {};
