import type { Meta, StoryObj } from "@storybook/react-vite";
import { AppRail, AppRuntimeProvider } from "@angee/ui";

import { chromeMenuItems } from "./chrome-fixtures";

const meta = {
  title: "Chrome/AppRail",
  component: AppRail,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof AppRail>;

export default meta;

type Story = StoryObj<typeof meta>;

function RailStory({ expanded }: { expanded: boolean }) {
  return (
    <AppRuntimeProvider
      runtime={{
        userPreferences: {
          preferences: {
            "chrome.rail": { order: [], defaultItemId: null, expanded },
          },
          setPreferences: async () => undefined,
        },
      }}
    >
      <div className="h-[34rem] overflow-hidden rounded-8 border border-border-subtle bg-canvas">
        <AppRail menuItems={chromeMenuItems} />
      </div>
    </AppRuntimeProvider>
  );
}

export const Collapsed: Story = {
  render: () => <RailStory expanded={false} />,
};

export const Expanded: Story = {
  parameters: { route: "/notes" },
  render: () => <RailStory expanded />,
};

export const Settings: Story = {
  parameters: { route: "/iam" },
  render: () => (
    <RailStory expanded />
  ),
};
