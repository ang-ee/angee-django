import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, DialogForm, FieldControl, FieldLabel, FieldRoot, Textarea } from "@angee/ui";

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
        <FieldRoot span="full">
          <FieldLabel required>Name</FieldLabel>
          <FieldControl defaultValue="Production API" />
        </FieldRoot>
        <FieldRoot span="full">
          <FieldLabel>Endpoint</FieldLabel>
          <FieldControl defaultValue="https://api.example.test/resources" />
        </FieldRoot>
        <FieldRoot span="full">
          <FieldLabel>Notes</FieldLabel>
          <FieldControl render={<Textarea defaultValue="Import contacts and account records." rows={3} />} />
        </FieldRoot>
      </DialogForm>
    </>
  );
}

export const FormDialog: Story = {
  render: () => <DialogFormDemo />,
};
