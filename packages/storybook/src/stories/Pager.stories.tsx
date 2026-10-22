import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { Pager } from "@angee/ui";

/**
 * The one pager: `range` reads "1-20 / 120 records" over pages; `record` reads
 * "n / N" over single records (the command band's pager). Numbers through
 * `formatNumber`.
 */
const meta = {
  title: "Primitives/Pager",
  component: Pager,
  parameters: { layout: "centered" },
} satisfies Meta<typeof Pager>;

export default meta;

type Story = StoryObj;

/** Pages of a collection with a page-size menu. */
export const Default: Story = {
  render: function Range() {
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(20);
    return <Pager page={page} pageSize={pageSize} total={1240} subject="records" onPageChange={setPage} onPageSizeChange={setPageSize} />;
  },
};

/** The record mode: the open record's position in its collection. */
export const Record: Story = {
  render: function Record() {
    const [page, setPage] = useState(3);
    return <Pager mode="record" page={page} pageSize={1} total={12} onPageChange={setPage} />;
  },
};

/** Unknown totals: the pager steps on the next flag alone. */
export const UnknownTotal: Story = {
  render: function UnknownTotal() {
    const [page, setPage] = useState(2);
    return <Pager page={page} pageSize={20} total={undefined} hasPrev={page > 1} hasNext onPageChange={setPage} />;
  },
};
