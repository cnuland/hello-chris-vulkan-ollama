#!/usr/bin/env bash
set -euo pipefail

# Ensure models dir exists and is writable (OpenShift random UID)
mkdir -p "${OLLAMA_MODELS:-/models}" || true
chmod 0777 "${OLLAMA_MODELS:-/models}" || true

# Start the Ollama server
/usr/local/bin/ollama "$@" &
SERVER_PID=$!

# Wait for server to become reachable
for i in $(seq 1 120); do
  if curl -fsS http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Optionally pre-pull the default model using the local API
if [[ "${OLLAMA_PULL_ON_START:-1}" == "1" && -n "${MODEL_NAME:-}" ]]; then
  echo "[entrypoint] Pre-pulling model: ${MODEL_NAME}"
  curl -fsS -X POST http://127.0.0.1:11434/api/pull \
    -H 'Content-Type: application/json' \
    -d "{\"name\":\"${MODEL_NAME}\"}" || true
fi

# Forward SIGTERM/SIGINT to server
trap 'kill -TERM ${SERVER_PID} 2>/dev/null || true' TERM INT

# Wait on the server process
wait ${SERVER_PID}
