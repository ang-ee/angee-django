import {
  Button,
  DialogForm,
  ErrorBanner,
  LabeledDescriptorField,
  errorMessage,
  type MutationDialogField,
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

type FieldErrors = Readonly<Record<string, readonly string[]>>;

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
  const [templateRef, setTemplateRef] = React.useState("");
  const [name, setName] = React.useState("");
  const [ttl, setTtl] = React.useState("");
  const [inputs, setInputs] = React.useState<Record<string, unknown>>({});
  const [fieldErrors, setFieldErrors] = React.useState<FieldErrors>({});
  const [formError, setFormError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const templates = React.useMemo(
    () =>
      [...(snapshot?.templates ?? [])]
        .filter((template) => template.kind === "workspace")
        .sort((left, right) => templateLabel(left).localeCompare(templateLabel(right))),
    [snapshot?.templates],
  );
  const selectedTemplate =
    templates.find((template) => template.ref === templateRef) ?? null;
  const templateInputs = React.useMemo(
    () =>
      (selectedTemplate?.inputs ?? []).filter(
        (input) => input.question && !input.generated,
      ),
    [selectedTemplate],
  );
  const templateField = React.useMemo<MutationDialogField>(
    () => ({
      name: "template",
      label: t("workspaces.create.template"),
      widget: "select",
      options: templates.map((template) => ({
        value: template.ref,
        label: templateLabel(template),
      })),
      placeholder: t("workspaces.create.templatePlaceholder"),
      required: true,
    }),
    [t, templates],
  );
  const nameField = React.useMemo<MutationDialogField>(
    () => ({
      name: "name",
      label: t("workspaces.create.name"),
      description: t("workspaces.create.nameDescription"),
    }),
    [t],
  );
  const ttlField = React.useMemo<MutationDialogField>(
    () => ({
      name: "ttl",
      label: t("workspaces.create.ttl"),
      description: t("workspaces.create.ttlDescription"),
    }),
    [t],
  );
  const busy = submitting || preflight.result.fetching || create.result.fetching;

  React.useEffect(() => {
    if (!open) reset();
    // The reset intentionally follows the dialog session boundary only. Template
    // snapshot updates while open must not erase answers the user is entering.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function reset(): void {
    setTemplateRef("");
    setName("");
    setTtl("");
    setInputs({});
    setFieldErrors({});
    setFormError(null);
    setSubmitting(false);
  }

  function selectTemplate(value: unknown): void {
    const nextRef = typeof value === "string" ? value : "";
    const template = templates.find((candidate) => candidate.ref === nextRef);
    setTemplateRef(nextRef);
    setInputs(initialTemplateInputs(template?.inputs ?? []));
    setFieldErrors({});
    setFormError(null);
  }

  function setInput(inputName: string, value: unknown): void {
    setInputs((current) => ({ ...current, [inputName]: value }));
    setFieldErrors((current) => withoutField(current, inputName));
    setFormError(null);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!templateRef || busy) return;
    setSubmitting(true);
    setFieldErrors({});
    setFormError(null);
    const object = workspaceCreateInput(templateRef, name, ttl, inputs);
    try {
      const checked = (await preflight.run({ input: object }))
        ?.workspaceCreatePreflight;
      if (!checked) throw new Error(t("workspaces.create.failed"));
      if (!checked.ok) {
        setFieldErrors(preflightErrors(checked, t("workspaces.create.required")));
        setFormError(t("workspaces.create.validationFailed"));
        return;
      }
      const workspace = (await create.run({ object }))?.insert_workspaces_one;
      if (!workspace) throw new Error(t("workspaces.create.failed"));
      // Pull the snapshot before navigating: the detail resolves by name from
      // the workspaces pane, and navigating ahead of the refresh bounces the
      // route back to the list.
      await Promise.resolve(refetchWorkspaces()).catch(() => undefined);
      onOpenChange(false);
      void navigate({ to: workspaceDetailPath(workspace.name) });
    } catch (cause) {
      setFormError(errorMessage(cause, t("workspaces.create.failed")));
    } finally {
      setSubmitting(false);
    }
  }

  const footer = (
    <>
      <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => onOpenChange(false)}>
        {t("workspaces.create.cancel")}
      </Button>
      <Button
        type="submit"
        variant="primary"
        size="sm"
        disabled={!templateRef || busy}
        loading={busy}
        loadingText={t("workspaces.create.submitting")}
      >
        {t("workspaces.create.submit")}
      </Button>
    </>
  );

  return (
    <DialogForm
      open={open}
      onOpenChange={onOpenChange}
      title={t("workspaces.create.title")}
      description={t("workspaces.create.description")}
      footer={footer}
      onSubmit={(event) => void submit(event)}
      size="lg"
      placement="prompt"
    >
      <LabeledDescriptorField
        field={templateField}
        value={templateRef}
        readOnly={busy}
        messages={fieldErrors.template ?? []}
        onChange={selectTemplate}
      />
      <LabeledDescriptorField
        field={nameField}
        value={name}
        readOnly={busy}
        messages={fieldErrors.name ?? []}
        onChange={(value) => {
          setName(typeof value === "string" ? value : "");
          setFieldErrors((current) => withoutField(current, "name"));
          setFormError(null);
        }}
      />
      <LabeledDescriptorField
        field={ttlField}
        value={ttl}
        readOnly={busy}
        messages={fieldErrors.ttl ?? []}
        onChange={(value) => {
          setTtl(typeof value === "string" ? value : "");
          setFieldErrors((current) => withoutField(current, "ttl"));
          setFormError(null);
        }}
      />
      {templateInputs.map((input) => (
        <LabeledDescriptorField
          key={input.name}
          field={templateInputField(input)}
          value={inputs[input.name]}
          readOnly={busy}
          messages={fieldErrors[input.name] ?? []}
          onChange={(value) => setInput(input.name, value)}
        />
      ))}
      <ErrorBanner description={formError} />
    </DialogForm>
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

function initialTemplateInputs(
  descriptors: readonly TemplateInputDescriptor[],
): Record<string, unknown> {
  return Object.fromEntries(
    descriptors
      .filter((input) => input.question && !input.generated)
      .map((input) => [input.name, initialTemplateInput(input)]),
  );
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
  template: string,
  name: string,
  ttl: string,
  inputs: Readonly<Record<string, unknown>>,
): WorkspaceCreateInput {
  const suppliedInputs = Object.fromEntries(
    Object.entries(inputs).filter(([, value]) => value != null),
  );
  const trimmedName = name.trim();
  const trimmedTtl = ttl.trim();
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
): FieldErrors {
  const errors: Record<string, string[]> = {};
  for (const field of preflight.missingRequired) {
    errors[field] = [...(errors[field] ?? []), requiredMessage];
  }
  for (const failure of preflight.invalidInputs) {
    errors[failure.field] = [...(errors[failure.field] ?? []), failure.reason];
  }
  return errors;
}

function withoutField(errors: FieldErrors, name: string): FieldErrors {
  if (!(name in errors)) return errors;
  const next = { ...errors };
  delete next[name];
  return next;
}
