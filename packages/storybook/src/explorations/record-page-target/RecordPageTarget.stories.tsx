import type { Meta, StoryObj } from "@storybook/react-vite";
import { RecordPageTarget } from "./RecordPageTarget";

const meta = {
  title: "Explorations/2026-10-20 Record page target",
  component: RecordPageTarget,
  parameters: { layout: "fullscreen" },
  args: { page: "bill" },
} satisfies Meta<typeof RecordPageTarget>;
export default meta;
type Story = StoryObj<typeof meta>;

export const VendorBill: Story = {};
export const VendorBillRunOpen: Story = { args: { runOpen: true } };
export const VendorBillRelated: Story = { args: { aside: "related" } };
export const VendorBillDiscussion: Story = { args: { aside: "discussion" } };
export const File: Story = { args: { page: "file" } };
export const FileRelated: Story = { args: { page: "file", aside: "related" } };
export const KnowledgePage: Story = { args: { page: "knowledge" } };
export const KnowledgePageRelated: Story = { args: { page: "knowledge", aside: "related" } };
export const NexusInbox: Story = { args: { page: "nexus", aside: "related" } };
export const NexusInboxMessage: Story = { args: { page: "nexus", messageOpen: true, aside: "timeline" } };
export const Anatomy: Story = { args: { anatomy: true } };
