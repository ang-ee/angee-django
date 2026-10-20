import type { Meta, StoryObj } from "@storybook/react-vite";
import { ActionMenu, ActionTrigger, ModalsHost } from "@angee/ui";

/**
 * The one verb menu: a trigger (icon-only `⋮`, or labelled) whose items are
 * `ActionTrigger`s. Contributed dialogs survive item activation and closing;
 * a blocked menu stays visible while the record is dirty.
 */
const meta = {
  title: "Toolbars/ActionMenu",
  component: ActionMenu,
  parameters: { layout: "centered", owner: [ActionMenu, ActionTrigger] },
} satisfies Meta<typeof ActionMenu>;

export default meta;

type Story = StoryObj;

const noop = () => undefined;

function Items() {
  return (
    <>
      <ActionTrigger glyph="copy" onClick={noop}>Copy link</ActionTrigger>
      <ActionTrigger glyph="file" onClick={noop}>Duplicate</ActionTrigger>
      <ActionTrigger glyph="users" disabled onClick={noop}>Share</ActionTrigger>
      <ActionTrigger glyph="trash" variant="danger" onClick={noop}>Move to trash</ActionTrigger>
    </>
  );
}

/** The command band's leading menu: icon-only, with an accessible name. */
export const Default: Story = {
  render: () => <ModalsHost><ActionMenu label={null} aria-label="Actions" glyph="more-vertical" size="iconSm"><Items /></ActionMenu></ModalsHost>,
};

/** A labelled trigger ("Actions") for list toolbars. */
export const Labelled: Story = {
  render: () => <ModalsHost><ActionMenu label="Actions" glyph={null} size="sm" variant="secondary"><Items /></ActionMenu></ModalsHost>,
};

/** A pane menu with a count on its trigger, and a blocked menu while the record is dirty. */
export const BadgeAndBlocked: Story = {
  render: () => (
    <ModalsHost>
      <div className="flex items-center gap-3">
        <ActionMenu label={null} aria-label="More" glyph="more-horizontal" size="iconSm" badge={3}><Items /></ActionMenu>
        <ActionMenu label={null} aria-label="Actions" glyph="more-vertical" size="iconSm" blocked><Items /></ActionMenu>
      </div>
    </ModalsHost>
  ),
};
