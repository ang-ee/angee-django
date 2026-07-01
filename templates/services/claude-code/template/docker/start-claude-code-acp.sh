#!/bin/sh
set -eu

if [ -n "${ANTHROPIC_MODEL:-}" ]; then
  export ANTHROPIC_CUSTOM_MODEL_OPTION="${ANTHROPIC_CUSTOM_MODEL_OPTION:-$ANTHROPIC_MODEL}"
  export ANTHROPIC_CUSTOM_MODEL_OPTION_NAME="${ANTHROPIC_CUSTOM_MODEL_OPTION_NAME:-$ANTHROPIC_MODEL}"

  model_family="$(printf '%s' "$ANTHROPIC_MODEL" | tr '[:upper:]' '[:lower:]')"
  case "$model_family" in
    *fable*)
      export ANTHROPIC_DEFAULT_FABLE_MODEL="${ANTHROPIC_DEFAULT_FABLE_MODEL:-$ANTHROPIC_MODEL}"
      ;;
    *opus*)
      export ANTHROPIC_DEFAULT_OPUS_MODEL="${ANTHROPIC_DEFAULT_OPUS_MODEL:-$ANTHROPIC_MODEL}"
      ;;
    *sonnet*)
      export ANTHROPIC_DEFAULT_SONNET_MODEL="${ANTHROPIC_DEFAULT_SONNET_MODEL:-$ANTHROPIC_MODEL}"
      ;;
    *haiku*)
      export ANTHROPIC_DEFAULT_HAIKU_MODEL="${ANTHROPIC_DEFAULT_HAIKU_MODEL:-$ANTHROPIC_MODEL}"
      ;;
  esac

  node <<'NODE'
const fs = require("fs");
const path = require("path");

const model = process.env.ANTHROPIC_MODEL;
if (!model) {
  process.exit(0);
}

const env = {
  ANTHROPIC_MODEL: model,
  ANTHROPIC_CUSTOM_MODEL_OPTION: process.env.ANTHROPIC_CUSTOM_MODEL_OPTION || model,
  ANTHROPIC_CUSTOM_MODEL_OPTION_NAME: process.env.ANTHROPIC_CUSTOM_MODEL_OPTION_NAME || model,
};

for (const name of [
  "ANTHROPIC_DEFAULT_FABLE_MODEL",
  "ANTHROPIC_DEFAULT_OPUS_MODEL",
  "ANTHROPIC_DEFAULT_SONNET_MODEL",
  "ANTHROPIC_DEFAULT_HAIKU_MODEL",
]) {
  if (process.env[name]) {
    env[name] = process.env[name];
  }
}

const home = process.env.HOME || "/home/node";
const claudeDir = path.join(home, ".claude");
fs.mkdirSync(claudeDir, { recursive: true });
fs.writeFileSync(
  path.join(claudeDir, "settings.json"),
  `${JSON.stringify(
    {
      model,
      availableModels: [model],
      enforceAvailableModels: true,
      env,
    },
    null,
    2,
  )}\n`,
);
NODE
fi

exec stdio-to-ws claude-agent-acp --port "${PORT:-3007}"
