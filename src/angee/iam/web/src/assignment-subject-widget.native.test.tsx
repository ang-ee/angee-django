// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { createUiTestProviders } from "@angee/ui/testing";
import { ActionFormProvider, AppRuntimeProvider, Button, DescriptorFieldList, ToastProvider, defaultWidgets, useActionForm, useFormSpecFields } from "@angee/ui";
import iam from "./index";
import type { IAMAssignmentSubjectsData } from "./documents";

const data: IAMAssignmentSubjectsData = {
  iam_assignment_subject_labels: [
    { subject: "auth/user:13", label: "Sam Reviewer" },
    { subject: "auth/group:7#member", label: "Reviewer team" },
    { subject: "auth/user:14", label: "Inactive Reviewer" },
    { subject: "auth/user:999", label: "Beyond Page" },
  ],
  users: [{ id: "usr_reviewer", username: "reviewer", first_name: "Sam", last_name: "Reviewer", email: "sam@example.test",
    display_name: "Sam Reviewer", is_active: true, assignment_subject: "auth/user:13" }],
  groups: [{ id: "igr_team", name: "Reviewer team", assignment_subject: "auth/group:7#member" },
    { id: "igr_other", name: "Other group", assignment_subject: "auth/group:8#member" }],
  users_aggregate: { aggregate: { count: 1 } }, groups_aggregate: { aggregate: { count: 1 } },
};
const providers = createUiTestProviders({ apiUrl: "test://subjects" });
const widgets = { ...defaultWidgets, ...iam.widgets };
afterEach(() => { cleanup(); providers.clearClients(); });

test("native subject chips resolve display names, narrow kinds, retain item messages and become read-only during submit", async () => {
  let finish: (() => void) | undefined;
  function Form() {
    const action = useActionForm({ defaultValues: { reviewers: ["auth/user:13", "auth/group:7#member", "auth/user:14", "auth/user:999"] }, submit: () => new Promise<{ status: "ok"; data: true }>((resolve) => {
      finish = () => resolve({ status: "ok", data: true });
    }) });
    const fields = useFormSpecFields({ type: "object", properties: { reviewers: {
      type: "array", widget: "assignmentUsers", title: "Reviewers", items: { type: "string" },
    } } });
    return <ActionFormProvider {...action.form}><DescriptorFieldList fields={fields} />
      <Button onClick={() => action.form.setError("reviewers.0", { type: "server", message: "Bad reviewer." })}>Issue</Button>
      <Button onClick={() => void action.run()}>Save</Button></ActionFormProvider>;
  }
  render(<providers.Provider dataProvider={{ custom: async () => ({ data }) }}>
    <AppRuntimeProvider runtime={{ widgets }}><ToastProvider><Form /></ToastProvider></AppRuntimeProvider>
  </providers.Provider>);
  expect(await screen.findByText("Sam Reviewer")).toBeTruthy();
  expect(await screen.findByText("Reviewer team")).toBeTruthy();
  expect(await screen.findByText("Inactive Reviewer")).toBeTruthy();
  expect(await screen.findByText("Beyond Page")).toBeTruthy();
  expect(screen.queryByText("auth/group:7#member")).toBeNull();
  expect(screen.queryByText("auth/user:13")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Issue" }));
  expect(await screen.findByText("Bad reviewer.")).toBeTruthy();
  const select = screen.getByRole("combobox", { name: "Reviewers" });
  fireEvent.click(select);
  expect(screen.queryByRole("option", { name: "Other group" })).toBeNull();
  fireEvent.keyDown(select, { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.queryByRole("combobox", { name: "Reviewers" })).toBeNull());
  expect(screen.getByText("Sam Reviewer")).toBeTruthy();
  expect(screen.getByText("Reviewer team")).toBeTruthy();
  await act(async () => { finish?.(); });
});

test("the all-subject offering includes groups through the existing widget registry", async () => {
  function Form() {
    const action = useActionForm({ defaultValues: { subjects: [] }, submit: async () => ({ status: "ok", data: true }) });
    const fields = useFormSpecFields({ type: "object", properties: { subjects: {
      type: "array", widget: "assignmentSubjects", title: "Subjects", items: { type: "string" },
    } } });
    return <ActionFormProvider {...action.form}><DescriptorFieldList fields={fields} /></ActionFormProvider>;
  }
  render(<providers.Provider dataProvider={{ custom: async () => ({ data }) }}>
    <AppRuntimeProvider runtime={{ widgets }}><ToastProvider><Form /></ToastProvider></AppRuntimeProvider>
  </providers.Provider>);
  const select = await screen.findByRole("combobox", { name: "Subjects" });
  await waitFor(() => expect(select.hasAttribute("disabled")).toBe(false));
  fireEvent.click(select);
  expect(await screen.findByRole("option", { name: "Other group" })).toBeTruthy();
});
