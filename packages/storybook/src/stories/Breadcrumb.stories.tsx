import type { Meta, StoryObj } from "@storybook/react-vite";
import { Breadcrumb, BreadcrumbProvider } from "@angee/base";

const meta = {
  title: "Chrome/Breadcrumb",
  component: Breadcrumb,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof Breadcrumb>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Trail: Story = {
  render: () => (
    <div className="w-[42rem] overflow-hidden rounded-lg border border-border-subtle">
      <BreadcrumbProvider
        items={[
          { label: "Console", to: "/notes" },
          { label: "Notes", to: "/notes" },
          { label: "Quarterly planning" },
        ]}
      >
        <Breadcrumb />
      </BreadcrumbProvider>
    </div>
  ),
};
