import type { Meta, StoryObj } from "@storybook/react-vite";
import { RecordActionBar } from "@angee/ui";

/**
 * The record's verbs: explicit placement wins, primary implies the toolbar, and
 * the form's band opts into a lone non-danger verb as a secondary button. A blocked verb stays
 * focusable and explains itself in a tooltip, never in a sentence beside it.
 */
const meta = {
  title: "Views/RecordActionBar",
  component: RecordActionBar,
  parameters: { layout: "padded" },
} satisfies Meta<typeof RecordActionBar>;

export default meta;

type Story = StoryObj;

const record = { id: "bill_9311", number: "BILL/2026/00006", state: "DRAFT", permissions: ["read", "write"] };
const run = async () => undefined;

/** Post as the primary verb, a danger verb, a disabled verb with its reason as a tooltip, then the icon-only menu. */
export const Default: Story = {
  render: () => (
    <div className="flex items-center gap-2 rounded-6 border border-border-subtle bg-sheet p-2">
      <RecordActionBar record={record} menuTrigger="icon" actions={[
        { id: "post", label: "Post", icon: "check", primary: true, placement: "toolbar", run },
        { id: "pay", label: "Register payment", placement: "toolbar", disabledReason: "Post the bill before registering a payment.", run },
        { id: "cancel", label: "Cancel bill", icon: "x", danger: true, placement: "toolbar", run },
        { id: "duplicate", label: "Duplicate", icon: "file", run },
        { id: "copy", label: "Copy link", icon: "copy", run },
        { id: "delete", label: "Delete", icon: "trash", danger: true, run, confirm: { title: "Delete this bill?", body: "The bill and its lines are removed." } },
      ]} />
    </div>
  ),
};

/** Only secondary verbs: the menu alone. */
export const MenuOnly: Story = {
  render: () => (
    <RecordActionBar record={record} menuTrigger="icon" actions={[
      { id: "duplicate", label: "Duplicate", icon: "file", run },
      { id: "copy", label: "Copy link", icon: "copy", run },
    ]} />
  ),
};

/** The form's band opts into a lone unmarked verb, without giving it primary emphasis. */
export const LoneVerb: Story = {
  render: () => (
    <RecordActionBar record={record} menuTrigger="icon" fillBand actions={[
      { id: "sync", label: "Sync now", icon: "refresh", run },
    ]} />
  ),
};

/** Primary is enough to put a verb on the toolbar, even without lone-verb promotion. */
export const PrimaryWithoutPlacement: Story = {
  render: () => (
    <RecordActionBar record={record} menuTrigger="icon" actions={[
      { id: "post", label: "Post", icon: "check", primary: true, run },
      { id: "copy", label: "Copy link", icon: "copy", run },
    ]} />
  ),
};
