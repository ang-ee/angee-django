import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { FilterClauseEditor, FilterClauseRow, RelationPicker, type FilterClause, type FilterClauseDraft, type FilterClauseField } from "@angee/ui";

const fields: readonly FilterClauseField[] = [
  { id: "title", label: "Title", type: "text", operators: ["iContains", "exact", "isNull"] },
  { id: "count", label: "Count", type: "number", operators: ["gte", "exact"] },
  { id: "active", label: "Active", type: "boolean", operators: ["exact"] },
  { id: "tags", label: "Tags", operators: ["inList", "jsonContains"] },
];
const meta = { title: "Toolbars/FilterClauseEditor", component: FilterClauseEditor } satisfies Meta<typeof FilterClauseEditor>;
export default meta;
type Story = StoryObj;

function EditorExample() {
  const [clauses, setClauses] = React.useState<FilterClause[]>([]);
  return <div className="grid max-w-sm gap-3">
    <FilterClauseEditor fields={fields} onSubmit={(clause) => setClauses([...clauses, clause])} />
    <pre aria-label="Submitted clauses">{JSON.stringify(clauses, null, 2)}</pre>
  </div>;
}
function RowExample() {
  const [value, setValue] = React.useState<FilterClauseDraft>({ fieldId: "title", operator: "iContains", value: "alpha" });
  return <div className="max-w-sm"><FilterClauseRow fields={fields} value={value} onChange={setValue} /></div>;
}
function RelationExample() {
  const [value, setValue] = React.useState<FilterClauseDraft>({ fieldId: "target", operator: "exact", value: "alpha" });
  const [clauses, setClauses] = React.useState<FilterClause[]>([]);
  const relationFields: readonly FilterClauseField[] = [{
    id: "target", label: "Target", operators: ["exact"],
    renderValue: ({ onValueChange, ...props }) => <RelationPicker {...props} onChange={onValueChange}
      options={[{ value: "alpha", label: "Alpha" }, { value: "beta", label: "Beta" }]} />,
  }];
  return <div className="grid max-w-sm gap-3">
    <p>Open the value picker, search for Beta, then press Enter. The selection changes; Add submits the clause.</p>
    <FilterClauseEditor fields={relationFields} value={value} onChange={setValue}
      onSubmit={(clause) => setClauses((current) => [...current, clause])} />
    <output aria-label="Selected relation">{value.value}</output>
    <pre aria-label="Submitted clauses">{JSON.stringify(clauses, null, 2)}</pre>
  </div>;
}
export const AddClauses: Story = { render: () => <EditorExample /> };
export const ControlledRow: Story = { render: () => <RowExample /> };
export const RelationPickerKeyboard: Story = { render: () => <RelationExample /> };
export const ReadOnly: Story = { render: () => <FilterClauseEditor fields={fields} value={{ fieldId: "count", operator: "gte", value: "3" }} onSubmit={() => {}} readOnly /> };
