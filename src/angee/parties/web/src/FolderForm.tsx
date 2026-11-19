import * as React from "react";
import { Field, Form, registerForm, type RegisteredFormProps } from "@angee/ui";

export const FOLDER_MODEL = "parties.Folder";

/**
 * A contact folder: the local mirror of one synced address book. Its directory
 * creates and names it, so `contact_folders` takes no writes and the form reads.
 * Field provenance, the Links tab's Sources and History compose onto the record
 * from their owners.
 */
function FolderForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  return (
    <Form {...props} resource={FOLDER_MODEL}>
      <Field name="name" title />
      <Field name="directory" />
    </Form>
  );
}

export const folderForm = registerForm(FOLDER_MODEL, FolderForm);
