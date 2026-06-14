import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Badge,
  Button,
  FieldRow,
  FormGrid,
  Glyph,
  Input,
  RecordView,
  SectionNav,
  Textarea,
  type SectionNavItem,
} from "@angee/base";

const sections: readonly SectionNavItem[] = [
  { id: "overview", label: "Overview", href: "#overview", active: true },
  { id: "activity", label: "Activity", href: "#activity" },
  { id: "files", label: "Files", href: "#files" },
];

const meta = {
  title: "Layouts/RecordView",
  component: RecordView,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof RecordView>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Detail: Story = {
  render: () => (
    <div className="h-screen bg-inset p-6">
      <RecordView className="overflow-hidden rounded-md border border-border-subtle">
        <RecordView.Header
          crumbs="Notes / Q3 review brief"
          title="Q3 review brief"
          description="Operational note · owned by Sofia"
          actions={
            <>
              <Button variant="secondary" size="sm">
                <Glyph name="archive" />
                Archive
              </Button>
              <Button variant="primary" size="sm">
                Save
              </Button>
            </>
          }
        />
        <RecordView.Toolbar start={<SectionNav items={sections} />} />
        <RecordView.Body>
          <FormGrid columns="two">
            <FieldRow label="Title" required>
              <Input defaultValue="Q3 review brief" />
            </FieldRow>
            <FieldRow label="Owner">
              <Input defaultValue="Sofia" />
            </FieldRow>
            <FieldRow label="Summary" span="full">
              <Textarea
                rows={4}
                defaultValue="Quarterly review of operational notes and release briefs."
              />
            </FieldRow>
          </FormGrid>
        </RecordView.Body>
        <RecordView.Aside collapse="never" width="md">
          <div className="space-y-4">
            <section>
              <h2 className="text-sm font-semibold text-fg">Details</h2>
              <dl className="mt-2 space-y-2 text-13">
                <div className="flex justify-between">
                  <dt className="text-fg-muted">Status</dt>
                  <dd>
                    <Badge tone="success">Active</Badge>
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-fg-muted">Words</dt>
                  <dd className="tabular-nums">2,840</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-fg-muted">Updated</dt>
                  <dd>Today</dd>
                </div>
              </dl>
            </section>
          </div>
        </RecordView.Aside>
        <RecordView.Footer className="justify-between">
          <span>Last saved 2 minutes ago</span>
          <span className="tabular-nums">Record 4 / 245</span>
        </RecordView.Footer>
      </RecordView>
    </div>
  ),
};
