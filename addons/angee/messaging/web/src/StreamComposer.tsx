import * as React from "react";
import {
  Button, Chip, MessageComposer, MessageComposerHint, Textarea, UploadDropTarget,
  messageComposerInputClassName, textRoleVariants,
} from "@angee/ui";

import { useMessagingT } from "./i18n";

export interface StreamComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  ready: boolean;
  submitKey: "enter" | "mod-enter";
  prompt: string;
  submitLabel: React.ReactNode;
  readerLine?: React.ReactNode;
  audience?: string;
  before?: React.ReactNode;
  inputBefore?: React.ReactNode;
  after?: React.ReactNode;
  actions?: React.ReactNode;
  attachments?: React.ReactNode;
  onFiles?: (files: FileList | readonly File[] | null) => void;
}

/** One shortcut policy for record chatter and both stream composers. */
export function handleMessageSubmitKey(event: React.KeyboardEvent<HTMLTextAreaElement>,
  submitKey: StreamComposerProps["submitKey"], ready: boolean, submit: () => void): void {
  if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
  if (submitKey === "mod-enter" && !event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  if (ready) submit();
}

/** The open stream composer shared by a thread post and a child-record create verb. */
export function StreamComposer({ value, onChange, onSubmit, disabled, ready, submitKey,
  prompt, submitLabel, readerLine, audience, before, inputBefore, after, actions, attachments, onFiles }: StreamComposerProps): React.ReactElement {
  const t = useMessagingT();
  const input = <Textarea value={value} onChange={(event) => onChange(event.currentTarget.value)}
    onKeyDown={(event) => handleMessageSubmitKey(event, submitKey, ready && !disabled, onSubmit)}
    rows={3} resize="none" readOnly={disabled} aria-label={t("composer.messageLabel")}
    placeholder={prompt} className={messageComposerInputClassName} />;
  const composer = <MessageComposer
    hint={<div className="flex flex-wrap items-center gap-2">
      {readerLine ? <span className={textRoleVariants({ role: "caption" })}>{readerLine}</span> : null}
      {audience ? <Chip tone="neutral" size="md">{t("stream.postingTo", { audience })}</Chip> : null}
      <MessageComposerHint submitKey={submitKey} />
    </div>}
    input={inputBefore ? <div className="space-y-2">{inputBefore}{input}</div> : input}
    attachments={attachments}
    actions={<>
      {actions}
      <Button type="submit" variant="primary" size="sm" disabled={disabled || !ready}>{submitLabel}</Button>
    </>} />;
  return <form className="mt-auto space-y-2" onSubmit={(event) => { event.preventDefault(); if (ready && !disabled) onSubmit(); }}>
    {before}
    {onFiles ? <UploadDropTarget disabled={disabled} overlay={t("composer.dropFiles")}
      overlayClassName="rounded-6" onFiles={onFiles}>{composer}</UploadDropTarget> : composer}
    {after}
  </form>;
}
