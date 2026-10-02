import { createUiTestProviders } from "@angee/ui/testing";
import type { DataProvider } from "@angee/refine";

import { AgentChatEndpointMutation, AgentRoster, RenderAgentPrompt } from "./documents";
import type { FakeAcpAgent } from "./acp-test-agent";

/** Native Refine reads/mutations over a fixture provider; only the server boundary is fake. */
export interface AcpProviderOptions {
  expiresAt?: () => string;
  mint?: () => Promise<void>;
  renderPrompt?: () => Promise<string>;
}
export function createAcpTestProviders(agent: FakeAcpAgent, protocolVersion: number = agent.version, options: AcpProviderOptions = {}) {
  return createUiTestProviders({
    queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
    dataProvider: {
      custom: async ({ meta }: Parameters<NonNullable<DataProvider["custom"]>>[0]) => {
        if (meta?.gqlMutation === AgentChatEndpointMutation) {
          await options.mint?.();
          return { data: { agent_chat_endpoint: {
            url: "/acp/agents/agt-1/", token: "", expires_at: options.expiresAt?.() ?? "", mcp_servers: {}, model_handle: "", protocol_version: protocolVersion,
          } } };
        }
        if (meta?.gqlMutation === RenderAgentPrompt) return { data: { render_agent_prompt: await options.renderPrompt?.() ?? "" } };
        if (meta?.gqlQuery === AgentRoster) return { data: { agents: [{
          id: "agt-1", name: "Scout", runtime_class: "OPENCODE", runtime_status: "RUNNING",
          is_template: false, updated_at: "2026-10-02", model: null,
        }] } };
        throw new Error("Unexpected authored test operation");
      },
    },
  });
}
