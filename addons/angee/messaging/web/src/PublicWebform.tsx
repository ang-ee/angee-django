import {
  Alert,
  Button,
  ErrorBanner,
  LabeledDescriptorField,
  LoadingPanel,
  deserializeFormSpec,
  formSpecInitialValues,
  useAppRuntime,
  type FormSpecFieldDescriptor,
  type WidgetMap,
  type WidgetFocusTarget,
} from "@angee/ui";
import * as React from "react";

import { useMessagingT, type MessagingT } from "./i18n";

interface PublicWebformDescription {
  slug: string;
  title: string;
  schema_version: number;
  fields: readonly FormSpecFieldDescriptor[];
  honeypot_field: string | null;
  success: { title: string | null; body: string | null };
}

interface PublicWebformError {
  code: string;
  status: number;
  message: string;
  fields: Readonly<Record<string, readonly string[]>>;
}

const WEBFORM_ERROR_KEYS: Readonly<Record<string, string>> = {
  invalid_request: "webform.error.invalid_request",
  body_too_large: "webform.error.body_too_large",
  unsupported_media_type: "webform.error.unsupported_media_type",
  rate_limited: "webform.error.rate_limited",
  invalid_token: "webform.error.invalid_token",
  guard_unavailable: "webform.error.guard_unavailable",
  form_unavailable: "webform.invalidSpec",
  form_changed: "webform.error.form_changed",
  invalid_submission: "webform.error.invalid_submission",
};

export interface PublicWebformProps {
  slug: string;
  showReceipt?: boolean;
  headingLevel?: "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
}

/** An embeddable public form; its caller owns the surrounding layout. */
export function PublicWebform({
  slug,
  showReceipt = true,
  headingLevel = "h1",
}: PublicWebformProps): React.ReactElement {
  const t = useMessagingT();
  const { widgets } = useAppRuntime();
  const [description, setDescription] = React.useState<PublicWebformDescription | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    setDescription(null);
    setError(null);
    void loadWebform(slug, controller.signal, t, widgets)
      .then(setDescription)
      .catch((loadError: unknown) => {
        if (controller.signal.aborted) return;
        setError(loadError instanceof Error ? loadError.message : t("webform.unavailable"));
      });
    return () => controller.abort();
  }, [slug, t, widgets]);

  if (error) {
    return <ErrorBanner title={t("webform.errorTitle")} description={error} />;
  }
  if (!description) {
    return <LoadingPanel density="inline" message={t("webform.loading")} />;
  }
  return (
    <LoadedPublicWebform
      description={description}
      showReceipt={showReceipt}
      headingLevel={headingLevel}
    />
  );
}

function LoadedPublicWebform({
  description,
  showReceipt,
  headingLevel: Heading,
}: {
  description: PublicWebformDescription;
  showReceipt: boolean;
  headingLevel: NonNullable<PublicWebformProps["headingLevel"]>;
}): React.ReactElement {
  const t = useMessagingT();
  const { fields } = description;
  const [values, setValues] = React.useState<Record<string, unknown>>(() =>
    formSpecInitialValues(fields, {}),
  );
  const honeypotId = React.useId();
  const [honeypot, setHoneypot] = React.useState("");
  const [fieldErrors, setFieldErrors] = React.useState<
    Readonly<Record<string, readonly string[]>>
  >({});
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [receiptId, setReceiptId] = React.useState<string | null>(null);
  const successRef = React.useRef<HTMLDivElement>(null);
  const controls = React.useRef(new Map<string, WidgetFocusTarget>());
  const focusError = React.useRef(false);
  const [submissionId] = React.useState(createSubmissionId);

  const fieldNames = new Set(fields.map((field) => field.name));
  const nonFieldErrors = Object.entries(fieldErrors).flatMap(([name, messages]) =>
    fieldNames.has(name) ? [] : messages,
  );

  React.useEffect(() => {
    if (receiptId) successRef.current?.focus();
  }, [receiptId]);

  React.useEffect(() => {
    if (submitting || !focusError.current) return;
    focusError.current = false;
    const first = fields.find((field) => fieldErrors[field.name]?.length && controls.current.has(field.name));
    if (first) controls.current.get(first.name)?.focus();
  }, [fieldErrors, fields, submitting]);

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setFieldErrors({});
    try {
      const nextReceipt = await submitWebform(
        description.slug,
        {
          submission_id: submissionId,
          answers: submissionAnswers(fields, values),
          ...(description.honeypot_field ? { [description.honeypot_field]: honeypot } : {}),
        },
        t,
      );
      setReceiptId(nextReceipt);
    } catch (submitError) {
      if (isPublicWebformError(submitError)) {
        const key = WEBFORM_ERROR_KEYS[submitError.code];
        setError(key ? t(key) : submitError.message);
        setFieldErrors(submitError.fields);
        focusError.current = submitError.status === 400;
      } else {
        setError(submitError instanceof Error ? submitError.message : t("webform.submitFailed"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (receiptId) {
    return (
      <Alert ref={successRef} tabIndex={-1} tone="success" title={<Heading>{description.success.title ?? t("webform.successTitle")}</Heading>}>
        <p>{description.success.body ?? t(showReceipt ? "webform.successBody" : "webform.successBodyNoReceipt")}</p>
        {showReceipt ? (
          <div className="mt-3 rounded-6 bg-inset px-3 py-2 font-mono text-xs text-fg" data-testid="webform-receipt">
            {receiptId}
          </div>
        ) : null}
      </Alert>
    );
  }

  return (
    <form className="grid gap-4" onSubmit={(event) => void submit(event)}>
      <header className="space-y-1">
        <Heading className="text-xl font-semibold text-fg">{description.title}</Heading>
        <p className="text-13 text-fg-muted">{t(showReceipt ? "webform.intro" : "webform.introNoReceipt")}</p>
      </header>
      {fields.map((field) => (
        <LabeledDescriptorField
          key={field.name}
          field={field}
          value={values[field.name]}
          readOnly={field.readOnly || submitting}
          messages={fieldErrors[field.name] ?? []}
          controlRef={(target) => {
            if (target) controls.current.set(field.name, target);
            else controls.current.delete(field.name);
          }}
          onChange={(value) => {
            setValues((current) => ({ ...current, [field.name]: value }));
            setFieldErrors((current) => {
              if (!(field.name in current)) return current;
              const next = { ...current };
              delete next[field.name];
              return next;
            });
          }}
        />
      ))}
      {description.honeypot_field ? (
        <div className="hidden" aria-hidden="true">
          <label htmlFor={honeypotId}>{t("webform.website")}</label>
          <input
            id={honeypotId}
            name={description.honeypot_field}
            tabIndex={-1}
            autoComplete="off"
            value={honeypot}
            onChange={(event) => setHoneypot(event.currentTarget.value)}
          />
        </div>
      ) : null}
      <ErrorBanner
        title={nonFieldErrors.length ? error : undefined}
        description={nonFieldErrors.length ? (
          <ul>{nonFieldErrors.map((message, index) => (
            <li key={`${index}:${message}`}>{message}</li>
          ))}</ul>
        ) : error}
      />
      <Button type="submit" variant="primary" loading={submitting} loadingText={t("webform.submitting")}>
        {t("webform.submit")}
      </Button>
    </form>
  );
}

async function loadWebform(
  slug: string,
  signal: AbortSignal,
  t: MessagingT,
  widgets: WidgetMap,
): Promise<PublicWebformDescription> {
  const response = await fetch(`/forms/${encodeURIComponent(slug)}`, {
    method: "GET",
    headers: { Accept: "application/json" },
    credentials: "omit",
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new Error(t(response.status === 404 ? "webform.unavailable" : "webform.loadFailed"));
  const value: unknown = await response.json();
  if (
    !isRecord(value) ||
    typeof value.slug !== "string" ||
    typeof value.title !== "string" ||
    typeof value.schema_version !== "number" ||
    !("form_schema" in value) ||
    !(value.honeypot_field === null || typeof value.honeypot_field === "string")
  ) {
    throw new Error(t("webform.invalidDescription"));
  }
  const success = value.success;
  if (success != null && (
    !isRecord(success) ||
    !(success.title == null || typeof success.title === "string") ||
    !(success.body == null || typeof success.body === "string")
  )) {
    throw new Error(t("webform.invalidDescription"));
  }
  let fields: readonly FormSpecFieldDescriptor[];
  try {
    fields = deserializeFormSpec(value.form_schema, widgets);
  } catch {
    throw new Error(t("webform.invalidSpec"));
  }
  return {
    slug: value.slug,
    title: value.title,
    schema_version: value.schema_version,
    fields,
    honeypot_field: value.honeypot_field,
    success: {
      title: isRecord(success) && typeof success.title === "string" ? success.title : null,
      body: isRecord(success) && typeof success.body === "string" ? success.body : null,
    },
  };
}

async function submitWebform(
  slug: string,
  body: Record<string, unknown>,
  t: MessagingT,
): Promise<string> {
  const response = await fetch(`/forms/${encodeURIComponent(slug)}`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    credentials: "omit",
    body: JSON.stringify(body),
  });
  const value: unknown = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = isRecord(value) && typeof value.message === "string"
      ? value.message
      : t("webform.submitFailed");
    throw {
      code: isRecord(value) && typeof value.error === "string" ? value.error : "",
      status: response.status,
      message,
      fields: isRecord(value) && isFieldErrors(value.fields) ? value.fields : {},
    } satisfies PublicWebformError;
  }
  if (!isRecord(value) || typeof value.submission_id !== "string") {
    throw new Error(t("webform.invalidReceipt"));
  }
  return value.submission_id;
}

function submissionAnswers(
  fields: readonly FormSpecFieldDescriptor[],
  values: Readonly<Record<string, unknown>>,
): Record<string, unknown> {
  const answers: Record<string, unknown> = {};
  for (const field of fields) {
    if (field.readOnly) continue;
    const value = values[field.name];
    if (!field.required && (value == null || value === "")) continue;
    answers[field.name] = value;
  }
  return answers;
}

function createSubmissionId(): string {
  return globalThis.crypto?.randomUUID?.() ??
    `submission-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function isPublicWebformError(value: unknown): value is PublicWebformError {
  return isRecord(value) &&
    typeof value.code === "string" &&
    typeof value.status === "number" &&
    typeof value.message === "string" &&
    isFieldErrors(value.fields);
}

function isFieldErrors(value: unknown): value is Readonly<Record<string, readonly string[]>> {
  return isRecord(value) && Object.values(value).every(
    (messages) => Array.isArray(messages) && messages.every((message) => typeof message === "string"),
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
