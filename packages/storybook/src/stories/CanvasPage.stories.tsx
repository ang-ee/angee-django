import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, CanvasPage } from "@angee/ui";

const meta = {
  title: "Layouts/CanvasPage",
  component: CanvasPage,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof CanvasPage>;

export default meta;

type Story = StoryObj<typeof meta>;

export const WithInspector: Story = {
  render: () => (
    <div className="h-screen bg-inset p-6">
      <CanvasPage className="overflow-hidden rounded-md border border-border-subtle">
        <CanvasPage.Toolbar
          start={
            <Button variant="secondary" size="sm">
              Add node
            </Button>
          }
          end={
            <Button variant="ghost" size="sm">
              Fit view
            </Button>
          }
        />
        <div className="grid h-full place-items-center bg-[radial-gradient(var(--color-border-subtle)_1px,transparent_1px)] [background-size:16px_16px]">
          <span className="text-13 text-fg-muted">Canvas surface</span>
        </div>
        <CanvasPage.Aside collapse="never" width="sm">
          <section className="space-y-2">
            <h2 className="text-sm font-semibold text-fg">Inspector</h2>
            <p className="text-13 text-fg-muted">No node selected.</p>
          </section>
        </CanvasPage.Aside>
      </CanvasPage>
    </div>
  ),
};
