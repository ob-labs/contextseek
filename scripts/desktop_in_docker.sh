#!/usr/bin/env bash
# Run a desktop build/check command inside the Tauri build container.
#
# The host distro (EL8) lacks webkit2gtk-4.1 and a new-enough glib, so the Rust
# GUI stack can't compile natively. This builds a Debian-based image once and
# runs the given command in it, with cargo registry + target caches persisted in
# named volumes so repeat runs are fast.
#
#   scripts/desktop_in_docker.sh check      # cargo check (fast Rust validation)
#   scripts/desktop_in_docker.sh build      # full package: SPA + sidecar + tauri
#   scripts/desktop_in_docker.sh <cmd...>   # arbitrary command in src-tauri/
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="contextseek-tauri-build"

command -v docker >/dev/null 2>&1 || { echo "error: docker is required" >&2; exit 1; }

# --network=host: the default Docker bridge network has broken DNS in this
# environment (container resolvers time out), while the host resolves fine.
echo "==> building image ${IMAGE} (cached after first run)"
docker build --network=host -t "${IMAGE}" -f "${REPO_ROOT}/desktop/tauri/Dockerfile" "${REPO_ROOT}/desktop/tauri"

run_in_container() {
  docker run --rm -t --network=host \
    -v "${REPO_ROOT}:/work" \
    -v contextseek-cargo-registry:/usr/local/cargo/registry \
    -v contextseek-tauri-target:/work/desktop/tauri/src-tauri/target \
    -w /work "${IMAGE}" bash -c "$1"
}

case "${1:-check}" in
  check)
    echo "==> cargo check"
    run_in_container "cd desktop/tauri/src-tauri && cargo check"
    ;;
  build)
    echo "==> full Linux package (SPA + sidecar + tauri build)"
    run_in_container "bash scripts/package_desktop.sh"
    echo "==> installers under desktop/tauri/src-tauri/target/release/bundle/"
    ;;
  *)
    run_in_container "cd desktop/tauri/src-tauri && $*"
    ;;
esac
