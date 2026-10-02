import { ActionFormProvider, AppRuntimeProvider, Button, DescriptorFieldList, Select, defaultWidgets, useActionForm, useActionFormValues, useFormSpecFields, wireFormSubmitResult, type FormSpecFieldDescriptor, type WidgetDefinition } from "@angee/ui";
import type { StoryObj } from "@storybook/react-vite";

export default { title: "Views/FormSubmission", parameters: { layout: "padded" } };

function Submission({ outcome }: { outcome: "OK" | "INVALID" | "CONFLICT" }) {
  const action = useActionForm({
    defaultValues: { name: "Entry", config: { target: "example", value: 1 } }, fieldNames: ["name", "config"],
    submit: () => wireFormSubmitResult({ status: outcome, data: { revision: 2 }, message: outcome === "CONFLICT" ? "The record changed. Your edits are retained." : "",
      issues: { fieldErrors: { "config.value": ["Choose a value above zero."] }, formErrors: [] } }),
  });
  const name = useActionFormValues(action.form, (values) => values.name);
  return <ActionFormProvider {...action.form}><div className="max-w-md space-y-3">
    <DescriptorFieldList fields={[{ name: "name", label: "Name", widget: "text" },
      { name: "config", label: "Configuration", widget: "object", objectTemplate: [
        { name: "target", label: "Target", widget: "text" }, { name: "value", label: "Value", widget: "number" },
      ] }]} />
    <output>{name}</output>
    {action.formError ? <p role="alert">{action.formError}</p> : null}
    <Button disabled={action.submitting} onClick={() => void action.run()}>Save</Button>
  </div></ActionFormProvider>;
}
export const Acknowledged: StoryObj = { render: () => <Submission outcome="OK" /> };
export const LocatedIssues: StoryObj = { render: () => <Submission outcome="INVALID" /> };
export const ConflictRetainsEdits: StoryObj = { render: () => <Submission outcome="CONFLICT" /> };

const relatedItems: WidgetDefinition<readonly string[]>["read"] = ({ field, value, onChange }) => {
  const item = (field as { itemTemplate?: FormSpecFieldDescriptor } | undefined)?.itemTemplate;
  return <div><p>Declared resource: {item?.relation?.resource}</p>
    <Select aria-label="Recipients" value={value?.[0] ?? ""} options={[{ value: "person_1", label: "Sample person" }]}
      onValueChange={(next) => onChange?.([next])} /></div>;
};
const relationWidgets = { ...defaultWidgets, relatedItems: { read: relatedItems, edit: relatedItems } };
function ItemRelationForm() {
  const fields = useFormSpecFields({ type: "object", properties: { recipients: {
    type: "array", widget: "relatedItems", items: { type: "string", relation: { resource: "contacts.Person" } },
  } } });
  const action = useActionForm({ defaultValues: { recipients: [] as string[] }, submit: (values) => ({ status: "ok", data: values }) });
  const values = useActionFormValues(action.form, (draft) => draft.recipients);
  return <ActionFormProvider {...action.form}><DescriptorFieldList fields={fields} /><output>{values.join(", ")}</output></ActionFormProvider>;
}
export const CustomListItemRelation: StoryObj = {
  render: () => <AppRuntimeProvider runtime={{ widgets: relationWidgets }}><ItemRelationForm /></AppRuntimeProvider>,
};
