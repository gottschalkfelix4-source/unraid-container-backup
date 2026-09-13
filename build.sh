#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
docker build -t unraid-container-backup:latest .
echo "Image gebaut: unraid-container-backup:latest"
