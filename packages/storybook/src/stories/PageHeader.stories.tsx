import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Badge,
  Button,
  Glyph,
  Page,
  PageBody,
  PageHeader,
} from "@angee/base";

const meta = {
  title: "Page/PageHeader",
  component: PageHeader,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof PageHeader>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Dense: Story = {
  render: () => (
    <Page height="auto" className="w-[52rem] overflow-hidden rounded-md border border-border-subtle">
      <PageHeader
        density="compact"
        crumbs="Health / Patients / PT-1842"
        eyebrow={<Badge tone="info">Open chart</Badge>}
        title="Jordan Ellis"
        description="68 yo M - MRN 481029 - primary care panel"
        actions={
          <>
            <Button variant="secondary" size="sm">
              <Glyph name="comments" />
              Message
            </Button>
            <Button variant="primary" size="sm">
              <Glyph name="activity" />
              New encounter
            </Button>
          </>
        }
      />
      <PageBody>
        <p className="text-13 text-fg-muted">
          Last encounter closed today at 10:42 AM.
        </p>
      </PageBody>
    </Page>
  ),
};
