// Persisted-session operations belong to the addon that contributes their mutations.

import { graphql } from "@angee/gql/console";

export const LatestAgentSession = graphql(`
  query LatestAgentSession($agentId: String!) {
    agent_sessions(
      where: { agent: { _eq: $agentId } }
      order_by: [{ created_at: desc }]
      limit: 1
    ) {
      id
      status
    }
  }
`);

export const AgentSessionTurns = graphql(`
  query AgentSessionTurns($sessionId: String!) {
    agent_sessions(where: { id: { _eq: $sessionId } }, limit: 1) {
      id
      status
      last_error
      agent {
        model {
          name
        }
      }
    }
    agent_turns(
      where: { session: { _eq: $sessionId } }
      order_by: [{ index: asc }]
    ) {
      id
      prompt
      updates
    }
  }
`);

export const StartAgentSession = graphql(`
  mutation StartAgentSession($agent: ID!, $context: JSON!) {
    start_agent_session(agent: $agent, context: $context) {
      id
    }
  }
`);

export const PostAgentMessage = graphql(`
  mutation PostAgentMessage($session: ID!, $text: String!) {
    post_agent_message(session: $session, text: $text) {
      id
    }
  }
`);
