import * as React from "react";
import { holdsPermission, type Row } from "@angee/metadata";
import { ActionFormDialog, Avatar, Button, Card, CardContent, Chip, Collapsible, EmptyState, ErrorBanner, MessageRow, RecordActionBar, SectionHeading, actionOutcomeSubmitResult, avatarInitials, useActionForm, useRuntimeViewAs, type RecordActionDescriptor, type ResourceListProps } from "@angee/ui";

import { RecordThreadConversation, type RecordThreadConversationProps } from "./RecordThreadConversation";
import { useMessagingT } from "./i18n";
import { StreamComposer } from "./StreamComposer";

export interface StreamSectionHeading {
  label: React.ReactNode;
  /** Consumer-translated server annotations; never derived from loaded rows. */
  counts?: React.ReactNode;
  summary?: React.ReactNode;
  hint?: React.ReactNode;
  audience?: React.ReactNode;
}

interface StreamChildBase {
  id: string;
  title: string;
  body?: string | null;
  authorLabel?: string | null;
  isSelf: boolean;
  audienceLabel?: string | null;
  createdAt: string;
  kindLabel?: string;
  prompt?: string;
  submitLabel?: string;
  readerLine?: string;
  /** The item's own verbs (pass on, resolve…), run against `record`; shown on the row. */
  actions?: readonly RecordActionDescriptor[];
  record?: Row | null;
  onActed?: () => void;
}

/** A child may have its own conversation, or be a standalone stream entry. */
export type StreamChildItem = StreamChildBase & (
  | { thread: false; verbs?: never }
  | { thread?: true; verbs: { root: string; reply: string } }
);

/** px-list owns this shape. The lane compiles against its merged createAction seam. */
export type StreamCreateAction = NonNullable<ResourceListProps["createAction"]>;

export type RecordThreadStreamSource =
  | { kind: "record"; modelLabel: string; recordId: string; conversation?: RecordThreadConversationProps["stream"] }
  | {
      kind: "children";
      modelLabel: string;
      items: readonly StreamChildItem[];
      empty?: { title: string; description?: React.ReactNode };
      createAction?: StreamCreateAction;
      /** Bind a single text argument of createAction to an always-open foot composer. */
      createComposer?: { bodyArg: string; prompt: string; readerLine?: string; audience?: string };
      onCreated?: () => void;
      submitKey?: RecordThreadConversationProps["submitKey"];
    };

export interface RecordThreadStreamProps {
  heading: StreamSectionHeading;
  source: RecordThreadStreamSource;
  submitKey?: RecordThreadConversationProps["submitKey"];
}

/** One stream presentation over a record thread or server-selected child rows. */
export function RecordThreadStream({ heading, source, submitKey }: RecordThreadStreamProps): React.ReactElement {
  const t = useMessagingT();
  const create = source.kind === "children" ? source.createAction : undefined;
  const canCreate = Boolean(create && (!create.permission || holdsPermission(create.record, create.permission)));
  const composer = source.kind === "children" ? source.createComposer : undefined;
  const createArgs = Array.isArray(create?.args) ? create.args : undefined;
  const inlineCreate = Boolean(create && composer && createArgs?.length === 1 &&
    createArgs[0]?.name === composer.bodyArg &&
    (createArgs[0].argKind === undefined || createArgs[0].argKind === "scalar"));

  return <section className="space-y-4">
    <SectionHeading label={heading.label} count={heading.counts} summary={heading.summary}
      hint={heading.hint} audience={heading.audience} />
    {source.kind === "record" ? <RecordThreadConversation modelLabel={source.modelLabel}
      recordId={source.recordId} submitKey={submitKey} stream={source.conversation ?? {}} /> : <>
      {source.items.length > 0 ? <Card><CardContent className="space-y-6">
        {source.items.map((item) => <ChildStreamItem key={item.id} item={item}
          modelLabel={source.modelLabel} submitKey={source.submitKey ?? submitKey} />)}
      </CardContent></Card> : <EmptyState icon="comments" title={source.empty?.title ?? t("stream.empty")}
        description={source.empty?.description ?? t("stream.emptyHint")} />}
      {canCreate && create ? inlineCreate && composer
        ? <InlineCreateComposer key={create.id} action={create} composer={composer}
            submitKey={source.submitKey ?? submitKey} onCreated={source.onCreated} />
        : <CreateActionDialogControl key={create.id} action={create} onCreated={source.onCreated} /> : null}
    </>}
  </section>;
}

function ChildStreamItem({ item, modelLabel, submitKey }: {
  item: StreamChildItem;
  modelLabel: string;
  submitKey?: RecordThreadConversationProps["submitKey"];
}): React.ReactElement {
  const t = useMessagingT();
  const author = item.isSelf ? t("message.you") : item.authorLabel || t("message.author");
  const audience = item.audienceLabel || t("composer.audience");
  return <div className="space-y-3">
    <ul>
      <MessageRow avatar={<Avatar size="sm" initials={avatarInitials(author)} alt={author} />}
        author={author} timestamp={item.createdAt} channel={<>
          <Chip tone="neutral" size="md">{audience}</Chip>
          {item.kindLabel ? <Chip tone="info">{item.kindLabel}</Chip> : null}
        </>}>
        <div className="font-medium">{item.title}</div>
        {item.body ? <div>{item.body}</div> : null}
      </MessageRow>
    </ul>
    {item.thread !== false ? <Collapsible.Root>
      <div className="flex flex-wrap items-center gap-2">
        <Collapsible.Trigger render={<Button type="button" variant="ghost" size="sm" />}>
          <Collapsible.Icon />
          {t("stream.showThread")}
        </Collapsible.Trigger>
        {item.actions?.length ? <RecordActionBar record={item.record ?? null} actions={item.actions}
          reload={item.onActed} /> : null}
      </div>
      <Collapsible.Panel>
        <RecordThreadConversation modelLabel={modelLabel} recordId={item.id}
          submitKey={submitKey} stream={{ audience: item.audienceLabel ?? undefined,
            prompt: item.prompt, submitLabel: item.submitLabel, readerLine: item.readerLine,
            verbs: item.verbs, messageTypes: ["comment"] }} />
      </Collapsible.Panel>
    </Collapsible.Root> : null}
  </div>;
}

function CreateActionDialogControl({ action, onCreated }: {
  action: StreamCreateAction;
  onCreated?: () => void;
}): React.ReactElement {
  const [open, setOpen] = React.useState(false);
  return <div className="flex justify-end">
    <Button type="button" variant="primary" size="sm" disabled={action.disabled}
      onClick={() => setOpen(true)}>{action.label}</Button>
    {open ? <ActionFormDialog action={action}
      context={{ record: action.record ?? null, selectedIds: [] }} open
      onOpenChange={setOpen} onSucceeded={onCreated} /> : null}
  </div>;
}

function InlineCreateComposer({ action, composer, submitKey = "enter", onCreated }: {
  action: StreamCreateAction;
  composer: { bodyArg: string; prompt: string; readerLine?: string; audience?: string };
  submitKey?: RecordThreadConversationProps["submitKey"];
  onCreated?: () => void;
}): React.ReactElement {
  const t = useMessagingT();
  const preview = useRuntimeViewAs();
  const [body, setBody] = React.useState("");
  const form = useActionForm<Record<string, string>>({
    fieldNames: [composer.bodyArg],
    submit: async (values) => {
      const result = await action.submit(values, { record: action.record ?? null, selectedIds: [] });
      return result && "status" in result ? result : actionOutcomeSubmitResult(result);
    },
    onSuccess: () => { setBody(""); onCreated?.(); },
  });
  const disabled = Boolean(action.disabled || form.submitting || preview.viewAs || preview.pending);
  const submit = () => {
    if (body.trim() && !disabled) {
      form.form.setValue(composer.bodyArg, body.trim());
      void form.run();
    }
  };
  return <StreamComposer value={body}
    onChange={(next) => { setBody(next); form.clearFieldError(composer.bodyArg); }}
    onSubmit={submit} disabled={disabled} ready={Boolean(body.trim())}
    submitKey={submitKey} prompt={composer.prompt} submitLabel={action.label}
    readerLine={composer.readerLine ?? (composer.audience ? undefined : t("composer.audience"))}
    audience={composer.audience}
    after={<ErrorBanner description={[...(form.fieldErrors[composer.bodyArg] ?? []), form.formError].filter(Boolean).join(" ") || null} />} />;
}
