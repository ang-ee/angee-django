import { useId, useState } from "react";
import { useFieldArray, useForm, useWatch } from "react-hook-form";
import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Button, FieldLabel, FieldRoot, Input, applyFormErrors, createKeyedEntry, keyedCollectionFromRecord, keyedCollectionToRecord,
  type KeyedCollection, type KeyedCollectionSnapshot,
} from "@angee/ui";

const meta = { title: "Views/KeyedCollection", parameters: { layout: "padded" } } satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;
type Value = { label: string };

function CollectionExample() {
  const id = useId();
  const form = useForm<{ entries: KeyedCollection<Value> }>({
    defaultValues: { entries: keyedCollectionFromRecord({ first: { label: "First entry" } }) },
  });
  const entries = useWatch({ control: form.control, name: "entries" });
  const { append, move } = useFieldArray({ control: form.control, name: "entries" });
  const [saved, setSaved] = useState<KeyedCollectionSnapshot<Value>>();
  return <form className="max-w-lg space-y-3" onSubmit={form.handleSubmit(({ entries: current }) => {
    form.clearErrors();
    const result = keyedCollectionToRecord(current);
    if (result.status === "ok") setSaved(result.data);
    else applyFormErrors(form, { ...result, issues: { ...result.issues,
      fieldErrors: Object.fromEntries(current.flatMap((entry, index) => {
        const messages = result.issues.fieldErrors[entry.clientId];
        return messages ? [[`entries.${index}.key`, messages]] : [];
      })),
    } });
  })}>
    {entries.map(({ clientId, key }, index) => <fieldset key={clientId} className="space-y-2 rounded-6 border p-3">
      <legend>Entry {clientId}</legend>
      <FieldRoot>
        <FieldLabel htmlFor={`${id}-${clientId}-key`}>Key</FieldLabel>
        <Input id={`${id}-${clientId}-key`} {...form.register(`entries.${index}.key`)}
          invalid={Boolean(form.formState.errors.entries?.[index]?.key)} />
        {form.formState.errors.entries?.[index]?.key?.message && <p role="alert">{form.formState.errors.entries[index]?.key?.message}</p>}
      </FieldRoot>
      <FieldRoot>
        <FieldLabel htmlFor={`${id}-${clientId}-label`}>Label</FieldLabel>
        <Input id={`${id}-${clientId}-label`} {...form.register(`entries.${index}.value.label`)} />
      </FieldRoot>
      <Button type="button" disabled={index === 0} aria-label={`Move ${key || "entry"} up`} onClick={() => move(index, index - 1)}>Move up</Button>
    </fieldset>)}
    <div className="flex gap-2">
      <Button type="button" onClick={() => {
        append(createKeyedEntry({ label: "New entry" }, `entry${entries.length + 1}`));
      }}>Add entry</Button>
      <Button type="submit">Save snapshot</Button>
    </div>
    <p className="text-sm text-fg-muted">Renaming a key keeps the entry identity. The saved snapshot and issue mapping stay fixed as you continue editing.</p>
    {saved && <output aria-label="Saved snapshot"><pre>{JSON.stringify({
      values: saved.values, clientIdByKey: Object.fromEntries(saved.clientIdByKey),
    }, null, 2)}</pre></output>}
  </form>;
}

export const EditableKeys: Story = { render: () => <CollectionExample /> };
