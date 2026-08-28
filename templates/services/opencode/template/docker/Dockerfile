# OpenCode ACP agent, exposed as a WebSocket by stdio-to-ws on :3007 (route.port).
# Central Caddy owns TLS + edge auth, so this image is just the agent + the bridge.
FROM node:22-slim

# Optional community Anthropic OAuth plugin for OpenCode. Empty by default: the image then
# supports API-key inference only. To enable Anthropic Personal-Plans (Claude Pro/Max) OAuth,
# build with `--build-arg OPENCODE_ANTHROPIC_AUTH_PLUGIN=<npm-package>` AND set
# ANGEE_OPENCODE_OAUTH_ENABLED in the server (see angee.agents.runtimes.OpenCodeRuntime).
# WARNING: such plugins are community-maintained and using a Pro/Max token in OpenCode is
# against Anthropic's terms (OpenCode removed built-in support in 1.3.0) — account-ban risk.
ARG OPENCODE_ANTHROPIC_AUTH_PLUGIN=""

# A minimal opencode config whose model is substituted from $OPENCODE_MODEL at runtime
# (opencode resolves `{env:VAR}` in config values). The service points OPENCODE_CONFIG at
# this file only when the agent has a model, so an empty handle never pins an empty model.
# When the auth plugin is set, it is installed and declared so opencode loads it (it bears
# and refreshes the OAuth token from the auth.json store).
RUN npm install -g opencode-ai stdio-to-ws \
    && mkdir -p /workspace /home/node/.local/share/opencode /home/node/.config/opencode \
    && if [ -n "$OPENCODE_ANTHROPIC_AUTH_PLUGIN" ]; then \
         npm install -g "$OPENCODE_ANTHROPIC_AUTH_PLUGIN" \
         && printf '{"model":"{env:OPENCODE_MODEL}","plugin":["%s"]}\n' "$OPENCODE_ANTHROPIC_AUTH_PLUGIN" \
              > /home/node/.config/opencode/angee.json; \
       else \
         printf '%s\n' '{"model":"{env:OPENCODE_MODEL}"}' > /home/node/.config/opencode/angee.json; \
       fi \
    && chown -R node:node /workspace /home/node/.local /home/node/.config

USER node
WORKDIR /workspace
EXPOSE 3007

# opencode's ACP server is the `opencode acp` subcommand, bridged to ws on :3007. When the
# service injects ANGEE_OPENCODE_AUTH_B64 (an OAuth credential, base64 of opencode's
# auth.json), decode it into OPENCODE_AUTH_CONTENT — which opencode reads as its auth store —
# before launch. base64 keeps the JSON opaque to YAML and the operator's secret substitution.
CMD ["sh", "-c", "if [ -n \"$ANGEE_OPENCODE_AUTH_B64\" ]; then export OPENCODE_AUTH_CONTENT=\"$(printf %s \"$ANGEE_OPENCODE_AUTH_B64\" | base64 -d)\"; fi; exec stdio-to-ws \"opencode acp\" --port 3007"]
