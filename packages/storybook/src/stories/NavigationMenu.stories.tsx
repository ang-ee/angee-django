import type { Meta, StoryObj } from "@storybook/react-vite";
import { Glyph, NavigationMenu } from "@angee/ui";

const meta = {
  title: "Primitives/NavigationMenu",
  component: NavigationMenu,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof NavigationMenu>;

export default meta;

type Story = StoryObj<typeof meta>;

export const HoverPanels: Story = {
  render: () => (
    <div className="rounded-6 bg-rail p-2">
      <NavigationMenu.Root defaultValue="workspace">
        <NavigationMenu.List>
          <NavigationMenu.Item>
            <NavigationMenu.Link href="#" active>
              <Glyph name="home" />
              Overview
            </NavigationMenu.Link>
          </NavigationMenu.Item>
          <NavigationMenu.Item value="workspace">
            <NavigationMenu.Trigger>
              <Glyph name="layout-dashboard" />
              Workspace
              <NavigationMenu.Icon />
            </NavigationMenu.Trigger>
            <NavigationMenu.Content>
              <div className="grid w-[28rem] grid-cols-2 gap-2">
                <NavigationCard
                  href="#"
                  icon="list"
                  title="Lists"
                  text="Review records, filters, and saved views."
                />
                <NavigationCard
                  href="#"
                  icon="activity"
                  title="Activity"
                  text="Track recent changes across the workspace."
                />
                <NavigationCard
                  href="#"
                  icon="star"
                  title="Favorites"
                  text="Jump back to pinned records and views."
                />
                <NavigationCard
                  href="#"
                  icon="file"
                  title="Documents"
                  text="Open reports, notes, and exports."
                />
              </div>
            </NavigationMenu.Content>
          </NavigationMenu.Item>
          <NavigationMenu.Item value="admin">
            <NavigationMenu.Trigger>
              <Glyph name="auth" />
              Admin
              <NavigationMenu.Icon />
            </NavigationMenu.Trigger>
            <NavigationMenu.Content>
              <div className="grid w-80 gap-1">
                <NavigationMenu.Link
                  href="#"
                  className="flex h-auto flex-col items-start gap-1 rounded-6 px-3 py-2 text-fg hover:bg-inset hover:text-fg"
                >
                  <span className="text-13 font-semibold">Identity</span>
                  <span className="text-xs text-fg-muted">
                    Users, groups, and permission assignments.
                  </span>
                </NavigationMenu.Link>
                <NavigationMenu.Link
                  href="#"
                  className="flex h-auto flex-col items-start gap-1 rounded-6 px-3 py-2 text-fg hover:bg-inset hover:text-fg"
                >
                  <span className="text-13 font-semibold">Resources</span>
                  <span className="text-xs text-fg-muted">
                    Imports, exports, and tiered manifests.
                  </span>
                </NavigationMenu.Link>
              </div>
            </NavigationMenu.Content>
          </NavigationMenu.Item>
        </NavigationMenu.List>
        <NavigationMenu.Portal>
          <NavigationMenu.Positioner sideOffset={8}>
            <NavigationMenu.Popup>
              <NavigationMenu.Viewport />
              <NavigationMenu.Arrow />
            </NavigationMenu.Popup>
          </NavigationMenu.Positioner>
        </NavigationMenu.Portal>
      </NavigationMenu.Root>
    </div>
  ),
};

function NavigationCard({
  href,
  icon,
  text,
  title,
}: {
  href: string;
  icon: string;
  text: string;
  title: string;
}) {
  return (
    <NavigationMenu.Link
      href={href}
      className="flex h-auto items-start gap-3 rounded-6 px-3 py-2 text-fg hover:bg-inset hover:text-fg"
    >
      <span className="mt-0.5 grid size-7 shrink-0 place-content-center rounded-6 bg-brand-soft text-brand-soft-text">
        <Glyph name={icon} />
      </span>
      <span className="min-w-0">
        <span className="block text-13 font-semibold">{title}</span>
        <span className="block text-xs leading-relaxed text-fg-muted">
          {text}
        </span>
      </span>
    </NavigationMenu.Link>
  );
}
