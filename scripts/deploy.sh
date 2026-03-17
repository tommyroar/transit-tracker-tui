#!/usr/bin/env bash
# Deploy transit-tracker to Nomad and register a GitHub deployment.
#
# Usage:
#   ./scripts/deploy.sh              # deploy (run or restart)
#   ./scripts/deploy.sh --restart    # restart existing allocation
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HCL_FILE="$PROJECT_DIR/transit-tracker.nomad.hcl"
NOMAD_JOB="transit-tracker"
ENVIRONMENT="nomad-custom"

WS_PORT=8000
WEB_PORT=8081

cd "$PROJECT_DIR"

# --- Nomad deploy ---
if [[ "${1:-}" == "--restart" ]]; then
  ALLOC_ID=$(nomad job status "$NOMAD_JOB" 2>/dev/null \
    | awk '/^Allocations/,0' | grep running | awk '{print $1}' | head -1)
  if [[ -z "$ALLOC_ID" ]]; then
    echo "No running allocation found, doing a full deploy instead"
    nomad job run "$HCL_FILE"
  else
    echo "Restarting allocation $ALLOC_ID"
    nomad alloc restart "$ALLOC_ID"
  fi
else
  echo "Deploying $NOMAD_JOB via Nomad..."
  nomad job run "$HCL_FILE"
fi

# --- GitHub deployment ---
REF=$(git rev-parse --abbrev-ref HEAD)
SHA=$(git rev-parse HEAD)
REPO=$(git remote get-url origin | sed -E 's|.*github\.com[:/]([^/]+/[^/.]+)(\.git)?$|\1|')

echo "Registering GitHub deployment for $REPO@$SHA..."

# Deactivate previous deployments for this environment
EXISTING=$(gh api "repos/$REPO/deployments?environment=$ENVIRONMENT" --jq '.[].id' 2>/dev/null || true)
for DEP_ID in $EXISTING; do
  gh api "repos/$REPO/deployments/$DEP_ID/statuses" -X POST \
    -f state=inactive >/dev/null 2>&1 || true
done

# Create new deployment
PAYLOAD=$(cat <<EOJSON
{
  "urls": {
    "websocket": "ws://127.0.0.1:$WS_PORT",
    "api_spec": "http://127.0.0.1:$WEB_PORT/spec",
    "api_json": "http://127.0.0.1:$WEB_PORT/api/spec",
    "nomad_ui": "http://127.0.0.1:4646/ui/jobs/$NOMAD_JOB"
  },
  "nomad_job": "$NOMAD_JOB",
  "services": {
    "websocket-server": {"port": $WS_PORT},
    "web-server": {"port": $WEB_PORT}
  }
}
EOJSON
)

DEP_ID=$(gh api "repos/$REPO/deployments" -X POST \
  -f ref="$REF" \
  -f environment="$ENVIRONMENT" \
  -F auto_merge=false \
  --jq '.id' \
  --input <(echo "{\"ref\":\"$REF\",\"environment\":\"$ENVIRONMENT\",\"auto_merge\":false,\"required_contexts\":[],\"payload\":$PAYLOAD}"))

gh api "repos/$REPO/deployments/$DEP_ID/statuses" -X POST \
  --input <(echo "{\"state\":\"success\",\"environment_url\":\"http://127.0.0.1:$WEB_PORT/spec\",\"log_url\":\"http://127.0.0.1:4646/ui/jobs/$NOMAD_JOB\"}") \
  >/dev/null

echo "Done! Deployment #$DEP_ID registered."
echo "  Nomad UI: http://127.0.0.1:4646/ui/jobs/$NOMAD_JOB"
echo "  API Spec: http://127.0.0.1:$WEB_PORT/spec"
echo "  WebSocket: ws://127.0.0.1:$WS_PORT"
