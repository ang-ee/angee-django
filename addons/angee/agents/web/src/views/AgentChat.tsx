import * as React from "react";
import { Alert, Card, CardContent, CardHeader, CardTitle } from "@angee/base";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  type TextMessagePartComponent,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import { Streamdown } from "streamdown";

import { useAcpRuntime, type AcpStatus } from "../useAcpRuntime";
import type { AgentChatView } from "../documents";

const STATUS_LABEL: Record<AcpStatus, string> = {
  idle: "Idle",
  connecting: "Connecting…",
  ready: "Ready",
  error: "Error",
  closed: "Disconnected",
};

/**
 * Chat with a running agent over ACP. Slotted at the agent detail beside the
 * provisioning panel for a non-template agent whose service is running; the browser
 * speaks ACP to the agent's routed WebSocket through the operator's central Caddy.
 * The composer is disabled until the session is ready.
 */
export function AgentChat({
  agentId,
  view,
}: {
  agentId: string;
  view: AgentChatView;
}): React.ReactElement {
  const { runtime, status, error } = useAcpRuntime(agentId, view);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Chat</CardTitle>
        <span className="text-12 text-fg-muted">{STATUS_LABEL[status]}</span>
      </CardHeader>
      <CardContent>
        <AssistantRuntimeProvider runtime={runtime}>
          {error !== null ? <Alert intent="danger">{error}</Alert> : null}
          <ThreadPrimitive.Root className="flex h-96 flex-col gap-3">
            <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto">
              <ThreadPrimitive.Empty>
                <p className="text-13 text-fg-muted">
                  Ask the agent about what you are looking at — it has the notes MCP tools.
                </p>
              </ThreadPrimitive.Empty>
              <ThreadPrimitive.Messages components={{ Message: ChatMessage }} />
            </ThreadPrimitive.Viewport>
            <ComposerPrimitive.Root className="flex items-end gap-2 border-t border-border pt-3">
              <ComposerPrimitive.Input
                className="flex-1 resize-none bg-transparent text-13 outline-none"
                placeholder={status === "ready" ? "Message the agent…" : "Connecting…"}
                disabled={status !== "ready"}
              />
              <ComposerPrimitive.Send
                disabled={status !== "ready"}
                className="text-13 text-accent disabled:text-fg-muted"
              >
                Send
              </ComposerPrimitive.Send>
            </ComposerPrimitive.Root>
          </ThreadPrimitive.Root>
        </AssistantRuntimeProvider>
      </CardContent>
    </Card>
  );
}

/** One message row: role label, markdown text via streamdown, and tool-call blocks. */
function ChatMessage(): React.ReactElement {
  return (
    <MessagePrimitive.Root className="mb-3 flex flex-col gap-1">
      <MessagePrimitive.Parts components={{ Text: AssistantText, tools: { Fallback: ToolBlock } }} />
    </MessagePrimitive.Root>
  );
}

/** Render assistant/user text as streamed markdown. */
const AssistantText: TextMessagePartComponent = ({ text }) => (
  <div className="text-13">
    <Streamdown>{text}</Streamdown>
  </div>
);

/** A compact fallback rendering for any tool call the agent runs, with its ACP status. */
const ToolBlock: ToolCallMessagePartComponent = ({ toolName, args }) => {
  const status = typeof args?.status === "string" ? args.status : undefined;
  return (
    <div className="rounded-md border border-border px-2 py-1 text-12 text-fg-muted">
      Tool: {toolName}
      {status !== undefined ? <span className="ml-1 text-fg-subtle">({status})</span> : null}
    </div>
  );
};
