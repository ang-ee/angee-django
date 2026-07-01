import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Button,
  Glyph,
  Page,
  PageBody,
  PageFooter,
  PageHeader,
} from "@angee/ui";

const meta = {
  title: "Page/PageFooter",
  component: PageFooter,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof PageFooter>;

export default meta;

type Story = StoryObj<typeof meta>;

export const StatusBar: Story = {
  render: () => (
    <Page height="auto" className="w-[48rem] overflow-hidden rounded-6 border border-border-subtle">
      <PageHeader density="compact" title="Draft note" />
      <PageBody>
        <p className="text-13 text-fg-muted">
          Owner, status, and body fields were changed.
        </p>
      </PageBody>
      <PageFooter className="justify-between">
        <span>Unsaved changes</span>
        <span className="flex items-center gap-2">
          <Button variant="secondary" size="sm">
            Discard
          </Button>
          <Button variant="primary" size="sm">
            <Glyph name="file" />
            Save
          </Button>
        </span>
      </PageFooter>
    </Page>
  ),
};
