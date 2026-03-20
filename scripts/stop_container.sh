#!/usr/bin/env bash
# =============================================================================
# Stop and remove the Transit Tracker Docker container.
#
# Usage:
#   scripts/stop_container.sh
# =============================================================================
set -euo pipefail

CONTAINER_NAME="transit-tracker"

# ---- Check if container exists ----
if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "No container named '$CONTAINER_NAME' found — nothing to stop."
    exit 0
fi

# ---- Stop container ----
echo "Stopping container '$CONTAINER_NAME'..."
docker stop "$CONTAINER_NAME"

# ---- Remove container ----
echo "Removing container '$CONTAINER_NAME'..."
docker rm "$CONTAINER_NAME"

echo "Container '$CONTAINER_NAME' stopped and removed."
