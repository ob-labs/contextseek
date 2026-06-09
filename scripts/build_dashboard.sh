#!/usr/bin/env bash
# Build the dashboard SPA for same-origin (single-process / desktop) serving.
# VITE_CTX_BASE="" makes the SPA call the API on its own origin (relative paths),
# which is what contextseek.http.server serves in desktop mode.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export VITE_CTX_BASE=""
npm --prefix "${REPO_ROOT}/dashboard" ci
npm --prefix "${REPO_ROOT}/dashboard" run build

echo "dashboard built -> ${REPO_ROOT}/dashboard/dist"
