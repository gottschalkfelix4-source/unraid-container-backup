#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
docker build -t dockguard:latest .
echo "Image gebaut: dockguard:latest"
