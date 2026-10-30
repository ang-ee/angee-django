import { Field, Form, registerForm, type RegisteredFormProps } from "@angee/ui";

/** Folder creation fields and ordinary saved-record fields share one registered form. */
function FolderForm({ resource: _resource, children, ...props }: RegisteredFormProps) {
  return (
    <Form {...props} resource="storage.Folder">
      {props.id == null ? <>
        <Field name="name" title />
        <Field name="drive" required />
        <Field name="parent" />
        <Field name="description" />
      </> : children}
    </Form>
  );
}

export const folderForm = registerForm("storage.Folder", FolderForm);
