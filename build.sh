#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Gleicher Name wie im Unraid-Template -> ein lokaler Build genügt der Vorlage,
# ohne dass etwas gepullt werden muss.
IMAGE="ghcr.io/gottschalkfelix4-source/unraid-container-backup:latest"

docker build -t "$IMAGE" -t dockguard:latest .
echo "Image gebaut: $IMAGE (Alias: dockguard:latest)"

if [ "${1:-}" = "--push" ]; then
  docker push "$IMAGE"
  echo "Image gepusht: $IMAGE"
fi
