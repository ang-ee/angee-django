import type { Meta, StoryObj } from "@storybook/react-vite";
import { TopMenu, type TopMenuTab } from "@angee/base";

const tabs: readonly TopMenuTab[] = [
  { id: "all", label: "All notes", icon: "list", filter: {} },
  { id: "starred", label: "Starred", icon: "star", filter: { isStarred: true } },
  { id: "archive", label: "Archive", icon: "archive", filter: { status: { exact: "ARCHIVED" } } },
];

const meta = {
  title: "Chrome/TopMenu",
  component: TopMenu,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof TopMenu>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <div className="rounded-md bg-rail p-2 text-on-rail">
      <TopMenu tabs={tabs} />
    </div>
  ),
};
