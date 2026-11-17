import type { CSSProperties } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { textRoleVariants } from "@angee/ui";
import { TypeRolesSheet } from "./design-system/token-sheets";
const meta = { title: "Foundations/Type roles", component: TypeRolesSheet, parameters: { layout: "fullscreen", owner: textRoleVariants } } satisfies Meta<typeof TypeRolesSheet>;
export default meta;
export const Default: StoryObj<typeof meta> = {};
/** A theme that names a display face: the title-like roles switch to it while body text keeps the sans face. */
export const DisplayFamily: StoryObj<typeof meta> = {
  render: () => <div style={{ "--font-family-display": 'Georgia, "Times New Roman", serif' } as CSSProperties}><TypeRolesSheet /></div>,
};
