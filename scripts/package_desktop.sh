#!/usr/bin/env bash
# End-to-end desktop packaging: build the SPA, bundle the Python sidecar, then
# run `tauri build`. Produces installers under
# desktop/tauri/src-tauri/target/release/bundle/.
#
# Requires: node + npm, a Python with contextseek[http,seekdb,openai] +
# pyinstaller, the Rust toolchain, and @tauri-apps/cli.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> 1/3 build dashboard SPA"
bash "${REPO_ROOT}/scripts/build_dashboard.sh"

echo "==> 2/3 bundle python sidecar"
bash "${REPO_ROOT}/scripts/build_python_runtime.sh"

echo "==> 3/3 tauri build"
# The SPA (dashboard/dist) is shipped as a Tauri bundle resource
# (tauri.conf.json `bundle.resources`); the Host passes its path to the sidecar
# via CTX_DASHBOARD_DIST at spawn time (see src-tauri/src/lib.rs). So the SPA
# must already be built (step 1) before this step.
cd "${REPO_ROOT}/desktop/tauri"
if command -v cargo-tauri >/dev/null 2>&1 || cargo tauri --version >/dev/null 2>&1; then
  cargo tauri build
else
  npm install
  npm run build
fi

echo "==> done. installers under desktop/tauri/src-tauri/target/release/bundle/"
