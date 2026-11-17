import { registerForm, type RegisteredFormProps } from "@angee/ui";
import { PROJECT_MODEL, TASK_MODEL } from "./resources";
import { projectRecordTabs, useProjectFormDeclaration } from "./project-declarations";
import { taskRecordTabs } from "./task-declarations";
import { useTaskFormDeclaration } from "./task-actions";

function ProjectForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  return useProjectFormDeclaration({}, { ...props, recordTabs: props.recordTabs ?? projectRecordTabs, defaultRecordTab: props.defaultRecordTab ?? "description" });
}

function TaskForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  return useTaskFormDeclaration({}, { ...props, recordTabs: props.recordTabs ?? taskRecordTabs, defaultRecordTab: props.defaultRecordTab ?? "description" });
}

/** Every generic project/task opener uses the existing domain declarations. */
export const projectForm = registerForm(PROJECT_MODEL, ProjectForm);
export const taskForm = registerForm(TASK_MODEL, TaskForm);
