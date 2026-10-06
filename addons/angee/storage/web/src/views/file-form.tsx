import { Field, FormView, registerForm, type RegisteredFormProps } from "@angee/ui";

/** A file opened as a record (a peek or relation dialog) is titled by name, with its preview beneath. */
function FileForm(props: RegisteredFormProps) {
  return <FormView {...props}><Field name="filename" title /></FormView>;
}

export const fileForm = registerForm("storage.File", FileForm);
