import type { Meta, StoryObj } from "@storybook/react-vite";
import { textRoleVariants } from "@angee/ui";
import { TypeRolesSheet } from "./design-system/token-sheets";
const meta = { title: "Foundations/Type roles", component: TypeRolesSheet, parameters: { layout: "fullscreen", owner: textRoleVariants } } satisfies Meta<typeof TypeRolesSheet>;
export default meta;
export const Default: StoryObj<typeof meta> = {};
