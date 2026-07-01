import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Page,
  PageAside,
  PageBody,
  PageHeader,
  SplitPane,
  SplitPaneHandle,
  SplitPanes,
} from "@angee/ui";

const meta = {
  title: "Page/SplitPanes",
  component: SplitPanes,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof SplitPanes>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Resizable: Story = {
  render: () => (
    <Page className="h-[30rem] w-[58rem] overflow-hidden rounded-6 border border-border-subtle">
      <PageHeader density="compact" title="File preview" />
      <SplitPanes>
        <SplitPane defaultSize="64%" minSize="35%">
          <PageBody
            className="grid place-items-center bg-inset text-13 text-fg-muted"
            gutter="none"
          >
            Preview pane
          </PageBody>
        </SplitPane>
        <SplitPaneHandle />
        <SplitPane defaultSize="36%" minSize="22%">
          <PageAside collapse="never" className="h-full" width="lg">
            <div className="space-y-4">
              <h2 className="text-sm font-semibold text-fg">Metadata</h2>
              <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-2 text-13">
                <dt className="text-fg-muted">Owner</dt>
                <dd>Alexis</dd>
                <dt className="text-fg-muted">Class</dt>
                <dd>Internal</dd>
                <dt className="text-fg-muted">Updated</dt>
                <dd>Today</dd>
              </dl>
            </div>
          </PageAside>
        </SplitPane>
      </SplitPanes>
    </Page>
  ),
};
