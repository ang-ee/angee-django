import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, DialogForm, FieldRow, Input, Textarea } from "@angee/ui";

const meta = {
  title: "Fragments/DialogForm",
  component: DialogForm,
  parameters: { layout: "centered" },
} satisfies Meta<typeof DialogForm>;

export default meta;

type Story = StoryObj;

function DialogFormDemo(): React.ReactElement {
  const [open, setOpen] = React.useState(true);

  return (
    <>
      <Button onClick={() => setOpen(true)} variant="primary">
        Open form
      </Button>
      <DialogForm
        description="Define the source used by resource imports."
        footer={
          <>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary">
              Create
            </Button>
          </>
        }
        onOpenChange={setOpen}
        onSubmit={(event) => {
          event.preventDefault();
          setOpen(false);
        }}
        open={open}
        title="Create source"
      >
        <FieldRow label="Name" required span="full">
          <Input defaultValue="Production API" />
        </FieldRow>
        <FieldRow label="Endpoint" span="full">
          <Input defaultValue="https://api.example.test/resources" />
        </FieldRow>
        <FieldRow label="Notes" span="full">
          <Textarea defaultValue="Import contacts and account records." rows={3} />
        </FieldRow>
      </DialogForm>
    </>
  );
}

export const FormDialog: Story = {
  render: () => <DialogFormDemo />,
};
