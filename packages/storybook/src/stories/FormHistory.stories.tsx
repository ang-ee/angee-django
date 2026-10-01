import { useId } from "react";
import { useForm } from "react-hook-form";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button, FieldLabel, FieldRoot, Input, useFormHistory } from "@angee/ui";

const meta = { title: "Views/FormHistory", parameters: { layout: "padded" } } satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;

function HistoryExample() {
  const id = useId();
  const form = useForm({ defaultValues: { title: "First title" } });
  const history = useFormHistory(form);
  const title = form.register("title");
  return <form className="max-w-md space-y-3" onSubmit={(event) => event.preventDefault()}
    onKeyDown={(event) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "z") return;
      event.preventDefault();
      if (event.shiftKey) history.redo(); else history.undo();
    }}>
    <FieldRoot>
      <FieldLabel htmlFor={id}>Title</FieldLabel>
      <Input {...title} id={id} onFocus={() => history.start("title")}
        onBlur={(event) => { void title.onBlur(event); history.commit("title"); }} />
    </FieldRoot>
    <p className="text-sm text-fg-muted">Edit the title, then leave the field to finish one undo group. Cmd/Ctrl+Z also works while editing.</p>
    <div className="flex flex-wrap gap-2">
      <Button type="button" disabled={!history.canUndo} onClick={history.undo}>Undo</Button>
      <Button type="button" disabled={!history.canRedo} onClick={history.redo}>Redo</Button>
      <Button type="button" onClick={() => history.perform(() => {
        form.setValue("title", "Another title", { shouldDirty: true });
      })}>Replace title</Button>
      <Button type="button" onClick={() => {
        form.reset(form.getValues());
        history.reset();
      }}>Accept changes</Button>
    </div>
    <output aria-live="polite">{form.formState.isDirty ? "Unsaved changes" : "No changes"}</output>
  </form>;
}

export const UndoAndRedo: Story = { render: () => <HistoryExample /> };
