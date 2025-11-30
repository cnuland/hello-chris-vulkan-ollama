#!/usr/bin/env bash
set -euo pipefail

ns="gpt-oss"
label="app=ollama-gpt-oss-120b"

pod=$(oc get pod -n "$ns" -l "$label" -o jsonpath='{.items[?(@.status.phase=="Running")].metadata.name}' | awk 'NR==1{print $1}')
route=$(oc get route -n "$ns" ollama-gpt-oss-120b -o jsonpath='{.spec.host}')
if [[ -z "${pod:-}" || -z "${route:-}" ]]; then
  echo "[monitor] Missing POD or ROUTE; aborting." >&2
  exit 1
fi

echo "[monitor] Monitoring pod=$pod route=https://$route (poll every 60s)"

while :; do
  log_tail=$(oc logs -n "$ns" "$pod" --tail=4000 2>/dev/null || true)
  tot_max=$(printf "%s" "$log_tail" | grep -o '"total":[0-9]\+' | awk -F: '{print $2}' | sort -nr | head -1)
  comp_last=$(printf "%s" "$log_tail" | grep -o '"completed":[0-9]\+' | tail -1 | cut -d: -f2)
  pct="unknown"
  if [[ -n "${tot_max:-}" && -n "${comp_last:-}" && "${tot_max:-0}" -gt 0 ]]; then
    pct=$(awk -v c="$comp_last" -v t="$tot_max" 'BEGIN{printf "%.1f", (c/t)*100}')
  fi
  ver=$(curl -sS "https://$route/api/version" || true)
  tags=$(curl -sS "https://$route/api/tags" || true)
  now=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  echo "[$now] progress=${pct}% completed=${comp_last:-na} total=${tot_max:-na} version=$ver tags=$tags"
  echo "$tags" | grep -q '"gpt-oss:120b"' && break || true
  sleep 60
done

echo "[monitor] Model present; running a test generation..."
curl -sS -X POST "https://$route/api/generate" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-oss:120b","prompt":"Say hello in one short sentence.","stream":false}'
echo
