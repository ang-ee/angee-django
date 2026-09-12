import {
  MutationDialog,
  type MutationDialogField,
  type MutationDialogValidationResult,
  type MutationDialogValues,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { useWorkspaceCreate, useWorkspacePreflight, toAnswerList } from "../../data/provision";
import { useOperatorSnapshot } from "../../data/transport";
import type {
  TemplateDescriptor,
  TemplateInputDescriptor,
  WorkspaceCreateInput,
} from "../../data/types";
import { useOperatorT } from "../../i18n";
import { workspaceDetailPath } from "../../lib/paths";

export interface WorkspaceCreateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Create a daemon workspace from one of the stack's workspace templates. */
export function WorkspaceCreateDialog({
  open,
  onOpenChange,
}: WorkspaceCreateDialogProps): React.ReactElement {
  const t = useOperatorT();
  const navigate = useNavigate();
  const { snapshot } = useOperatorSnapshot({ templates: true });
  // The detail view resolves its record from the workspaces snapshot; the
  // post-create navigation must not race the next snapshot push (see submit).
  const { refetch: refetchWorkspaces } = useOperatorSnapshot({ workspaces: true });
  const preflight = useWorkspacePreflight();
  const create = useWorkspaceCreate();

  const templates = React.useMemo(
    () =>
      [...(snapshot?.templates ?? [])]
        .filter((template) => template.kind === "workspace")
        .sort((left, right) => templateLabel(left).localeCompare(templateLabel(right))),
    [snapshot?.templates],
  );
  const inputNames = React.useMemo(
    () => [...new Set(templates.flatMap((template) => editableTemplateInputs(template.inputs).map((input) => input.name)))],
    [templates],
  );
  const fields = React.useMemo<readonly MutationDialogField[]>(() => [
    {
      name: "template",
      label: t("workspaces.create.template"),
      widget: "select",
      options: templates.map((template) => ({
        value: template.ref,
        label: templateLabel(template),
      })),
      placeholder: t("workspaces.create.templatePlaceholder"),
      required: true,
      prefill: (value) => templateInputPrefill(templates, inputNames, value),
    },
    {
      name: "name",
      label: t("workspaces.create.name"),
      description: t("workspaces.create.nameDescription"),
    },
    {
      name: "ttl",
      label: t("workspaces.create.ttl"),
      description: t("workspaces.create.ttlDescription"),
    },
    ...inputNames.map((name): MutationDialogField => ({
      name,
      label: name,
      showWhen: (values) => templateInputFor(templates, values.template, name) !== null,
      resolve: (values) => {
        const input = templateInputFor(templates, values.template, name);
        return input ? templateInputField(input) : { name, label: name };
      },
    })),
  ], [inputNames, t, templates]);

  const validate = React.useCallback(async (
    object: WorkspaceCreateInput,
  ): Promise<MutationDialogValidationResult | null> => {
    const checked = (await preflight.run({ input: object }))?.workspaceCreatePreflight;
    if (!checked) throw new Error(t("workspaces.create.failed"));
    if (checked.ok) return null;
    return {
      fieldErrors: preflightErrors(checked, t("workspaces.create.required")),
      formError: t("workspaces.create.validationFailed"),
    };
  }, [preflight, t]);

  const submit = React.useCallback(async (object: WorkspaceCreateInput) => {
    const workspace = (await create.run({ object }))?.insert_workspaces_one;
    if (!workspace) throw new Error(t("workspaces.create.failed"));
    // Pull the snapshot before navigating: detail resolves by name from the
    // workspaces pane and would otherwise bounce back to the list.
    await Promise.resolve(refetchWorkspaces()).catch(() => undefined);
    return workspace;
  }, [create, refetchWorkspaces, t]);

  return (
    <MutationDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("workspaces.create.title")}
      description={t("workspaces.create.description")}
      fields={fields}
      submitLabel={t("workspaces.create.submit")}
      submittingLabel={t("workspaces.create.submitting")}
      cancelLabel={t("workspaces.create.cancel")}
      errorFallback={t("workspaces.create.failed")}
      parseValues={(values) => workspaceCreateInput(templates, values)}
      validate={validate}
      onSubmit={submit}
      onSubmitted={(workspace) => {
        void navigate({ to: workspaceDetailPath(workspace.name) });
      }}
      size="lg"
      placement="prompt"
    />
  );
}

function templateLabel(template: TemplateDescriptor): string {
  return template.name || template.ref;
}

function templateInputField(input: TemplateInputDescriptor): MutationDialogField {
  const type = input.type?.toLowerCase();
  return {
    name: input.name,
    label: input.name,
    required: input.required,
    kind:
      type === "bool" || type === "boolean"
        ? "switch"
        : type === "int" || type === "integer"
          ? "integer"
          : "text",
  };
}

function editableTemplateInputs(
  descriptors: readonly TemplateInputDescriptor[],
): TemplateInputDescriptor[] {
  return descriptors.filter((input) => input.question && !input.generated);
}

function templateInputFor(
  templates: readonly TemplateDescriptor[],
  templateRef: unknown,
  name: string,
): TemplateInputDescriptor | null {
  if (typeof templateRef !== "string") return null;
  const template = templates.find((candidate) => candidate.ref === templateRef);
  return editableTemplateInputs(template?.inputs ?? []).find((input) => input.name === name) ?? null;
}

function templateInputPrefill(
  templates: readonly TemplateDescriptor[],
  inputNames: readonly string[],
  templateRef: unknown,
): Record<string, unknown> {
  const cleared = Object.fromEntries(inputNames.map((name) => [name, undefined]));
  if (typeof templateRef !== "string") return cleared;
  const template = templates.find((candidate) => candidate.ref === templateRef);
  return {
    ...cleared,
    ...Object.fromEntries(
      editableTemplateInputs(template?.inputs ?? [])
        .map((input) => [input.name, initialTemplateInput(input)]),
    ),
  };
}

function initialTemplateInput(input: TemplateInputDescriptor): unknown {
  const type = input.type?.toLowerCase();
  if (type === "bool" || type === "boolean") {
    return input.default != null
      ? ["true", "1", "yes", "y"].includes(input.default.toLowerCase())
      : false;
  }
  if ((type === "int" || type === "integer") && input.default == null) {
    return null;
  }
  return input.default ?? "";
}

function workspaceCreateInput(
  templates: readonly TemplateDescriptor[],
  values: MutationDialogValues,
): WorkspaceCreateInput {
  const template = typeof values.template === "string" ? values.template : "";
  const selected = templates.find((candidate) => candidate.ref === template);
  const inputs = Object.fromEntries(
    editableTemplateInputs(selected?.inputs ?? []).map((input) => [input.name, values[input.name]]),
  );
  const suppliedInputs = Object.fromEntries(
    Object.entries(inputs).filter(([, value]) => value != null),
  );
  const trimmedName = typeof values.name === "string" ? values.name.trim() : "";
  const trimmedTtl = typeof values.ttl === "string" ? values.ttl.trim() : "";
  return {
    template,
    inputs: toAnswerList(suppliedInputs),
    ...(trimmedName ? { name: trimmedName } : {}),
    ...(trimmedTtl ? { ttl: trimmedTtl } : {}),
  };
}

function preflightErrors(
  preflight: {
    missingRequired: readonly string[];
    invalidInputs: ReadonlyArray<{ field: string; reason: string }>;
  },
  requiredMessage: string,
): Readonly<Record<string, readonly string[]>> {
  const errors: Record<string, string[]> = {};
  for (const field of preflight.missingRequired) {
    errors[field] = [...(errors[field] ?? []), requiredMessage];
  }
  for (const failure of preflight.invalidInputs) {
    errors[failure.field] = [...(errors[failure.field] ?? []), failure.reason];
  }
  return errors;
}
