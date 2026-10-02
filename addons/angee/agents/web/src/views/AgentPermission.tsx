import { Button, ChatBubble, MutationDialog, ToolFallback, mutationDialogValueCodecs } from "@angee/ui";
import { RequestPermissionSubject } from "@agentclientprotocol/sdk/experimental/v2";
import type { ReactElement } from "react";

import type { AcpPermission, AcpRuntime } from "../useAcpRuntime";
import { useAgentsT } from "../i18n";

/** ACP owns the choices; shared chat and dialog owners collect the user's answer. */
export function AgentPermission({ permission, answer }: {
  permission: AcpPermission;
  answer: AcpRuntime["answerPermission"];
}): ReactElement {
  const t = useAgentsT();
  const request = permission.request;
  const title = "title" in request ? request.title : request.toolCall.title ?? t("permission.title");
  const subject = "subject" in request ? request.subject : undefined;
  const tool = "toolCall" in request ? request.toolCall : subject && RequestPermissionSubject.isToolCall(subject) ? subject.toolCall : undefined;
  const command = subject && RequestPermissionSubject.isCommand(subject) ? subject : undefined;
  return (
    <ChatBubble role="system">
      <div role="group" aria-label={t("permission.title")} className="space-y-2">
        <p role="alert">{title}</p>
        {"description" in request && request.description ? <p>{request.description}</p> : null}
        {tool ? <ToolFallback toolName={tool.title ?? title} status="pending" input={tool.rawInput} /> : null}
        {command ? <ToolFallback toolName={title} status="pending" input={{ command: command.command, cwd: command.cwd }} /> : null}
        <div className="flex flex-wrap gap-2">
          {request.options.map((option) => {
            const reject = option.kind === "reject_once" || option.kind === "reject_always";
            const label = option.kind === "allow_once" ? t("permission.approve")
              : option.kind === "allow_always" ? t("permission.approveAlways")
                : option.kind === "reject_once" ? t("permission.reject")
                  : option.kind === "reject_always" ? t("permission.rejectAlways") : option.name;
            return reject && permission.protocolVersion === 2 ? (
              <MutationDialog
                key={option.optionId}
                title={title}
                trigger={<Button size="sm" variant="secondary">{label}</Button>}
                fields={[{ name: "reason", label: t("permission.reason"), widget: "text" }]}
                submitLabel={label}
                parseValues={(values) => ({ reason: mutationDialogValueCodecs.string(values.reason) ?? "" })}
                onSubmit={({ reason }) => {
                  answer(permission.id, option.optionId, reason);
                  return { status: "ok", data: undefined };
                }}
              />
            ) : (
              <Button key={option.optionId} size="sm" variant={reject ? "secondary" : "primary"} onClick={() => answer(permission.id, option.optionId)}>
                {label}
              </Button>
            );
          })}
        </div>
      </div>
    </ChatBubble>
  );
}
