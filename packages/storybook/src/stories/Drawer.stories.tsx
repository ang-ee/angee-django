import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, Drawer, DrawerContent } from "@angee/ui";

const meta = {
  title: "Primitives/Drawer",
  component: DrawerContent,
  parameters: {
    layout: "padded",
  },
  argTypes: {
    side: {
      control: "select",
      options: ["right", "left", "top", "bottom"],
    },
  },
  args: {
    side: "right",
  },
} satisfies Meta<typeof DrawerContent>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Playground: Story = {
  render: ({ side }) => (
    <div className="min-h-80">
      <Drawer.Root defaultOpen>
        <Drawer.Trigger className="inline-flex h-btn-md items-center justify-center rounded-6 border border-border-strong bg-inset px-3 text-13 font-medium text-fg outline-none transition-colors hover:bg-sheet focus-visible:focus-ring">
          Open drawer
        </Drawer.Trigger>
        <Drawer.Portal>
          <Drawer.Backdrop />
          <Drawer.Content side={side}>
            <Drawer.Header>
              <Drawer.Title>Record details</Drawer.Title>
              <Drawer.Description>
                Review the current record before saving changes.
              </Drawer.Description>
              <Drawer.Close className="absolute right-3 top-3" />
            </Drawer.Header>
            <Drawer.Body>
              <div className="grid gap-4">
                <section className="space-y-1">
                  <h3 className="text-13 font-semibold text-fg">Owner</h3>
                  <p>Sofia Marin</p>
                </section>
                <section className="space-y-1">
                  <h3 className="text-13 font-semibold text-fg">Status</h3>
                  <p>Ready for review</p>
                </section>
                <section className="space-y-1">
                  <h3 className="text-13 font-semibold text-fg">Notes</h3>
                  <p>
                    The drawer uses Base UI Dialog behavior with edge-positioned
                    Angee surface styling.
                  </p>
                </section>
              </div>
            </Drawer.Body>
            <Drawer.Footer>
              <Button variant="ghost" size="sm">
                Cancel
              </Button>
              <Button variant="primary" size="sm">
                Save
              </Button>
            </Drawer.Footer>
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>
    </div>
  ),
};
