import type { Meta, StoryObj } from "@storybook/react-vite";
import { ReuseReport } from "./design-system/composition-viewer";
const meta = { title: "Reports/Reuse", component: ReuseReport, parameters: { layout: "fullscreen" } } satisfies Meta<typeof ReuseReport>;
export default meta;
export const Default: StoryObj<typeof meta> = {};
