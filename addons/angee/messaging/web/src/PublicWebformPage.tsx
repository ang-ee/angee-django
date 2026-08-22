import {
  Button,
  ErrorBanner,
  LabeledDescriptorField,
  LazyBoundary,
  LoadingPanel,
  PublicLayout,
  formSpecInitialValues,
  useFormSpecFields,
  type FormSpecFieldDescriptor,
} from "@angee/ui";
import { useParams } from "@tanstack/react-router";
import * as React from "react";

import { useMessagingT } from "./i18n";

interface PublicWebformDescription {
  slug: string;
  title: string;
  schema_version: number;
  form_schema: unknown;
}

interface PublicWebformError {
  message: string;
  fields: Readonly<Record<string, readonly string[]>>;
}

export function PublicWebformPage(): React.ReactElement {
  const { slug = "" } = useParams({ strict: false }) as { slug?: string };
  const t = useMessagingT();
  const [description, setDescription] = React.useState<PublicWebformDescription | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const controller = new AbortController();
    setDescription(null);
    setError(null);
    void loadWebform(slug, controller.signal)
      .then(setDescription)
      .catch((loadError: unknown) => {
        if (controller.signal.aborted) return;
        setError(loadError instanceof Error ? loadError.message : t("webform.unavailable"));
      });
    return () => controller.abort();
  }, [slug, t]);

  if (error) {
    return (
      <PublicLayout>
        <ErrorBanner title={t("webform.errorTitle")} description={error} />
      </PublicLayout>
    );
  }
  if (!description) {
    return (
      <PublicLayout>
        <LoadingPanel density="inline" message={t("webform.loading")} />
      </PublicLayout>
    );
  }
  return (
    <LazyBoundary
      pending={null}
      fallback={<PublicLayout><ErrorBanner description={t("webform.invalidSpec")} /></PublicLayout>}
      resetKey={`${description.slug}:${description.schema_version}`}
    >
      <LoadedPublicWebform description={description} />
    </LazyBoundary>
  );
}

function LoadedPublicWebform({
  description,
}: {
  description: PublicWebformDescription;
}): React.ReactElement {
  const t = useMessagingT();
  const fields = useFormSpecFields(description.form_schema);
  const [values, setValues] = React.useState<Record<string, unknown>>(() =>
    formSpecInitialValues(fields, {}),
  );
  const [honeypot, setHoneypot] = React.useState("");
  const [fieldErrors, setFieldErrors] = React.useState<
    Readonly<Record<string, readonly string[]>>
  >({});
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [receipt, setReceipt] = React.useState<string | null>(null);
  const [submissionId] = React.useState(createSubmissionId);

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setFieldErrors({});
    try {
      const nextReceipt = await submitWebform(description.slug, {
        submission_id: submissionId,
        answers: submissionAnswers(fields, values),
        _website: honeypot,
      });
      setReceipt(nextReceipt);
    } catch (submitError) {
      if (isPublicWebformError(submitError)) {
        setError(submitError.message);
        setFieldErrors(submitError.fields);
      } else {
        setError(submitError instanceof Error ? submitError.message : t("webform.submitFailed"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (receipt) {
    return (
      <PublicLayout>
        <div className="space-y-3 text-center">
          <h1 className="text-xl font-semibold text-fg">{t("webform.successTitle")}</h1>
          <p className="text-13 text-fg-muted">{t("webform.successBody")}</p>
          <div className="rounded-6 bg-inset px-3 py-2 font-mono text-xs text-fg" data-testid="webform-receipt">
            {receipt}
          </div>
        </div>
      </PublicLayout>
    );
  }

  return (
    <PublicLayout>
      <form className="grid gap-4" onSubmit={(event) => void submit(event)}>
        <header className="space-y-1">
          <h1 className="text-xl font-semibold text-fg">{description.title}</h1>
          <p className="text-13 text-fg-muted">{t("webform.intro")}</p>
        </header>
        {fields.map((field) => (
          <LabeledDescriptorField
            key={field.name}
            field={field}
            value={values[field.name]}
            readOnly={field.readOnly || submitting}
            messages={fieldErrors[field.name] ?? []}
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
        <div className="hidden" aria-hidden="true">
          <label htmlFor="webform-website">Website</label>
          <input
            id="webform-website"
            name="website"
            tabIndex={-1}
            autoComplete="off"
            value={honeypot}
            onChange={(event) => setHoneypot(event.currentTarget.value)}
          />
        </div>
        <ErrorBanner
          description={error ?? fieldErrors.answers?.join(", ") ?? null}
        />
        <Button type="submit" variant="primary" loading={submitting} loadingText={t("webform.submitting")}>
          {t("webform.submit")}
        </Button>
      </form>
    </PublicLayout>
  );
}

async function loadWebform(slug: string, signal: AbortSignal): Promise<PublicWebformDescription> {
  const response = await fetch(`/forms/${encodeURIComponent(slug)}`, {
    method: "GET",
    headers: { Accept: "application/json" },
    credentials: "omit",
    cache: "no-store",
    signal,
  });
  if (!response.ok) throw new Error(response.status === 404 ? "This form is unavailable." : "Could not load this form.");
  const value: unknown = await response.json();
  if (!isRecord(value) || typeof value.slug !== "string" || typeof value.title !== "string" || typeof value.schema_version !== "number" || !("form_schema" in value)) {
    throw new Error("The form server returned an invalid description.");
  }
  return {
    slug: value.slug,
    title: value.title,
    schema_version: value.schema_version,
    form_schema: value.form_schema,
  };
}

async function submitWebform(
  slug: string,
  body: Record<string, unknown>,
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
      : "Could not submit this form.";
    throw {
      message,
      fields: isRecord(value) && isFieldErrors(value.fields) ? value.fields : {},
    } satisfies PublicWebformError;
  }
  if (!isRecord(value) || typeof value.submission_id !== "string") {
    throw new Error("The form server returned an invalid receipt.");
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
  return isRecord(value) && typeof value.message === "string" && isFieldErrors(value.fields);
}

function isFieldErrors(value: unknown): value is Readonly<Record<string, readonly string[]>> {
  return isRecord(value) && Object.values(value).every(
    (messages) => Array.isArray(messages) && messages.every((message) => typeof message === "string"),
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
