# Claude Code ACP agent, exposed as a WebSocket by stdio-to-ws on :3007 (route.port).
# The operator's central Caddy terminates TLS + edge auth, so this image carries no
# Caddy and no token verifier — just the agent and the stdio<->ws bridge.
#
# NOTE: confirm the ACP adapter package against the current ecosystem before first
# build (the ACP claude adapter has shipped under more than one name).
FROM node:22-slim

RUN npm install -g @zed-industries/claude-code-acp stdio-to-ws \
    && mkdir -p /workspace /home/node/.claude \
    && chown -R node:node /workspace /home/node/.claude

USER node
WORKDIR /workspace
EXPOSE 3007

CMD ["stdio-to-ws", "claude-code-acp", "--port", "3007"]
