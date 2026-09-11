import type { Meta, StoryObj } from "@storybook/react-vite";
import type { ResourceProps } from "@angee/refine";
import { InboxExplorerV4 } from "./InboxExplorerV4";

const menu: ResourceProps[] = [
  { name: "menu:nexus", list: "/nexus", meta: { menuId: "nexus", label: "Nexus", icon: "comments", appRoot: true } },
  { name: "menu:nexus.inbox", list: "/nexus/inbox", meta: { menuId: "nexus.inbox", label: "Inbox", icon: "comments", parent: "menu:nexus" } },
  { name: "menu:files", list: "/files", meta: { menuId: "files", label: "Files", icon: "files" } },
  { name: "menu:notes", list: "/notes", meta: { menuId: "notes", label: "Notes", icon: "notes" } },
  { name: "menu:iam", list: "/iam", meta: { menuId: "iam", label: "Permissions", icon: "auth", group: "platform" } },
];

const meta = {
  title: "Nexus/Inbox Explorer V4 — console study",
  component: InboxExplorerV4,
  parameters: {
    layout: "fullscreen", route: "/nexus/inbox", angeeRoutes: ["/nexus", "/nexus/inbox"], angeeResources: menu,
    docs: { description: { component: "Desktop composition study using the actual ConsoleLayout: sender TreeView in the primary pane, ResourceList inline navigation from conversation results to a full-width Message in the main canvas, and the pinned Related view in the shell secondary pane. Message and Related stay visible together. One search/coverage band. Navigation and production reads are intentionally deferred. V1–V3 are preserved in the private study archive." } },
  },
  decorators: [(Story) => <div className="-m-6"><Story /></div>],
} satisfies Meta<typeof InboxExplorerV4>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Overview: Story = { args: { related: { target: "file:fil_invoice", origin: "msg_anna_d1" } } };
export const AllConversations: Story = { args: { sender: "" } };
export const ReadingMessage: Story = { args: { message: "msg_anna_d1", related: { target: "file:fil_invoice", origin: "msg_anna_d1" } } };
export const SearchResults: Story = { args: { sender: "", search: "invoice" } };
export const RelatedFile: Story = { args: { coverage: { platform: "email" }, related: { target: "file:fil_invoice", origin: "msg_mail3" } } };
export const CrossAccountReading: Story = { args: { coverage: { platform: "email" }, message: "msg_anna_d1", related: { target: "file:fil_invoice", origin: "msg_mail3" } } };
export const FullConversation: Story = { args: { thread: "thr_family", message: "msg_fam3", search: "invoice" } };
export const SuggestedIdentity: Story = { args: { sender: "hdl_sofia", message: "msg_sofia_tg" } };
