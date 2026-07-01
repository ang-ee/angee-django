import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Badge,
  Button,
  Glyph,
  Page,
  PageAside,
  PageBody,
  PageFooter,
  PageHeader,
  PageToolbar,
  SearchInput,
  SectionNav,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Toolbar,
  STATUS_TONES,
  stateToneFromValue,
  type SectionNavItem,
} from "@angee/ui";

const navItems: readonly SectionNavItem[] = [
  { id: "all", label: "All notes", href: "#all", active: true },
  { id: "mine", label: "Assigned to me", href: "#mine" },
  { id: "archive", label: "Archive", href: "#archive" },
];

const records = [
  ["Q3 review brief", "Active", "Sofia", "2,840", "Today"],
  ["Clinic rollout notes", "Draft", "Alexis", "1,260", "Yesterday"],
  ["Access audit outline", "Active", "Mara", "980", "May 28"],
  ["Storage policy memo", "Archived", "Eoin", "3,420", "May 21"],
] as const;

const meta = {
  title: "Page/Page",
  component: Page,
  parameters: {
    layout: "fullscreen",
  },
} satisfies Meta<typeof Page>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Frame: Story = {
  render: () => (
    <div className="h-screen bg-canvas p-6 text-fg">
      <Page className="h-full overflow-hidden rounded-6 border border-border-subtle">
        <PageHeader
          crumbs="Notes / All notes"
          title="Notes"
          description="Operational notes, release briefs, and system references."
          actions={
            <>
              <Button variant="secondary" size="sm">
                <Glyph name="archive" />
                Export
              </Button>
              <Button variant="primary" size="sm">
                <Glyph name="file" />
                New note
              </Button>
            </>
          }
        />
        <PageToolbar
          start={
            <>
              <Button variant="primary" size="sm">
                <Glyph name="file" />
                Create
              </Button>
              <div className="w-full max-w-[35rem]">
                <SearchInput
                  surface="sheet"
                  placeholder="Filter notes..."
                  defaultValue="status: active"
                />
              </div>
            </>
          }
          end={
            <>
              <Toolbar surface="inline" aria-label="View">
                <Toolbar.Button buttonSize="iconSm" aria-label="List view">
                  <Glyph name="list" />
                </Toolbar.Button>
                <Toolbar.Button buttonSize="iconSm" aria-label="Starred view">
                  <Glyph name="star" />
                </Toolbar.Button>
              </Toolbar>
              <Button variant="ghost" size="sm">
                Actions
              </Button>
            </>
          }
        />
        <div className="flex min-h-0 flex-1">
          <PageBody gutter="none">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead sticky>Title</TableHead>
                  <TableHead sticky>Status</TableHead>
                  <TableHead sticky>Owner</TableHead>
                  <TableHead sticky className="text-right">
                    Words
                  </TableHead>
                  <TableHead sticky>Updated</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {records.map(([title, status, owner, words, updated]) => (
                  <TableRow key={title} interactive>
                    <TableCell className="font-medium">{title}</TableCell>
                    <TableCell>
                      <Badge tone={stateToneFromValue(status, STATUS_TONES)}>{status}</Badge>
                    </TableCell>
                    <TableCell>{owner}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {words}
                    </TableCell>
                    <TableCell className="text-fg-muted">{updated}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </PageBody>
          <PageAside collapse="never" width="md">
            <div className="space-y-5">
              <section>
                <h2 className="text-sm font-semibold text-fg">Inspector</h2>
                <p className="mt-1 text-13 text-fg-muted">
                  245 records across 8 owners.
                </p>
              </section>
              <SectionNav orientation="vertical" items={navItems} />
            </div>
          </PageAside>
        </div>
        <PageFooter className="justify-between">
          <span>4 visible records</span>
          <span className="tabular-nums">1-50 / 245</span>
        </PageFooter>
      </Page>
    </div>
  ),
};

export const InContentRegion: Story = {
  name: "In Content Region",
  render: () => (
    // Page is layout-agnostic: mounted inside the console content region, that
    // region owns the scroll and canvas background, so Page runs height="auto"
    // / overflow="visible" and never opens a second scroller.
    <div className="grid h-screen grid-rows-[auto_1fr] bg-inset">
      <div className="flex h-12 shrink-0 items-center border-b border-border-subtle bg-sheet px-4 text-sm font-semibold text-fg">
        Workspace / Notes
      </div>
      <main className="min-h-0 min-w-0 overflow-auto bg-canvas">
        <Page height="auto" overflow="visible" className="min-h-full">
        <PageHeader
          headingLevel={2}
          crumbs="Workspace / Notes"
          title="Notes"
          description="Operational notes, release briefs, and system references."
          actions={
            <Button variant="primary" size="sm">
              <Glyph name="file" />
              New note
            </Button>
          }
        />
        <PageToolbar start={<SectionNav items={navItems} />} />
        <PageBody as="section">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Owner</TableHead>
                <TableHead className="text-right">Words</TableHead>
                <TableHead>Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {records.map(([title, status, owner, words, updated]) => (
                <TableRow key={title} interactive>
                  <TableCell className="font-medium">{title}</TableCell>
                  <TableCell>
                    <Badge tone={stateToneFromValue(status, STATUS_TONES)}>{status}</Badge>
                  </TableCell>
                  <TableCell>{owner}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {words}
                  </TableCell>
                  <TableCell className="text-fg-muted">{updated}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </PageBody>
        </Page>
      </main>
    </div>
  ),
};
