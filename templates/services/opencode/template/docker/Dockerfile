# OpenCode ACP agent, exposed as a WebSocket by stdio-to-ws on :3007 (route.port).
# Central Caddy owns TLS + edge auth, so this image is just the agent + the bridge.
FROM node:22-slim

# A minimal opencode config whose model is substituted from $OPENCODE_MODEL at runtime
# (opencode resolves `{env:VAR}` in config values). The service points OPENCODE_CONFIG at
# this file only when the agent has a model, so an empty handle never pins an empty model.
# opencode advertises models over a non-standard ACP field the browser can't drive, so the
# model is selected here in the container rather than over the session.
RUN npm install -g opencode-ai stdio-to-ws \
    && mkdir -p /workspace /home/node/.local/share/opencode /home/node/.config/opencode \
    && printf '%s\n' '{"model":"{env:OPENCODE_MODEL}"}' > /home/node/.config/opencode/angee.json \
    && chown -R node:node /workspace /home/node/.local /home/node/.config

USER node
WORKDIR /workspace
EXPOSE 3007

# opencode's ACP server is the `opencode acp` subcommand, bridged to ws on :3007.
CMD ["sh", "-c", "stdio-to-ws \"opencode acp\" --port 3007"]
