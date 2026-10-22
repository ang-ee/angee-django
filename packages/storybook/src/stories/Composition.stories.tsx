import type { Meta, StoryObj } from "@storybook/react-vite";
import { useArgs } from "storybook/preview-api";
import { CompositionViewer, compositionRootArgument, compositionRootArguments } from "./design-system/composition-viewer";
const meta = {
  title: "Reports/Composition", component: CompositionViewer, parameters: { layout: "fullscreen" },
  args: { root: compositionRootArgument("ui:FormView") },
  argTypes: { root: { mapping: compositionRootArguments, control: "text" } },
  render: function Render(args) {
    const [, updateArgs] = useArgs();
    return <CompositionViewer {...args} onRootChange={root => updateArgs({ root: compositionRootArgument(root) })} />;
  },
} satisfies Meta<typeof CompositionViewer>;
export default meta;
export const Default: StoryObj<typeof meta> = {};
