import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Button,
  Glyph,
  Page,
  PageBody,
  PageToolbar,
  SearchInput,
  Toolbar,
} from "@angee/ui";

const meta = {
  title: "Page/PageToolbar",
  component: PageToolbar,
  parameters: {
    layout: "centered",
  },
} satisfies Meta<typeof PageToolbar>;

export default meta;

type Story = StoryObj<typeof meta>;

export const Controls: Story = {
  render: () => (
    <Page height="auto" className="w-[58rem] overflow-hidden rounded-6 border border-border-subtle">
      <PageToolbar
        start={
          <>
            <Button variant="primary" size="sm">
              <Glyph name="file" />
              New
            </Button>
            <SearchInput
              className="max-w-[35rem]"
              surface="sheet"
              placeholder="Filter records..."
            />
          </>
        }
        end={
          <>
            <Toolbar surface="inline" aria-label="View mode">
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
      <PageBody>
        <p className="text-13 text-fg-muted">
          32 active records match the current filter.
        </p>
      </PageBody>
    </Page>
  ),
};
