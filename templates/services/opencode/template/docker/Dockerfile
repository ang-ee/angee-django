# OpenCode ACP agent, exposed as a WebSocket by stdio-to-ws on :3007 (route.port).
# Central Caddy owns TLS + edge auth, so this image is just the agent + the bridge.
FROM node:22-slim

RUN npm install -g opencode-ai stdio-to-ws \
    && mkdir -p /workspace /home/node/.local/share/opencode \
    && chown -R node:node /workspace /home/node/.local

USER node
WORKDIR /workspace
EXPOSE 3007

# opencode's ACP server is the `opencode acp` subcommand, bridged to ws on :3007.
CMD ["sh", "-c", "stdio-to-ws \"opencode acp\" --port 3007"]
