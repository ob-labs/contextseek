#!/usr/bin/env bash
# Bundle the Python sidecar into a single self-contained executable so the
# desktop app needs no pre-installed Python. The output is placed where Tauri's
# `externalBin` expects it: desktop/tauri/src-tauri/binaries/
#   contextseek-desktop-server-<target-triple>[.exe]
#
# Requires: a Python with `contextseek[http,seekdb,openai]` + pyinstaller.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3}"
OUT_DIR="${REPO_ROOT}/desktop/tauri/src-tauri/binaries"
NAME="contextseek-desktop-server"

# Resolve Rust target triple so the filename matches Tauri's sidecar convention.
TRIPLE="${TAURI_TARGET_TRIPLE:-$(rustc -Vv 2>/dev/null | sed -n 's/^host: //p')}"
if [ -z "${TRIPLE}" ]; then
  echo "error: cannot determine target triple (set TAURI_TARGET_TRIPLE or install rustc)" >&2
  exit 1
fi

EXE=""
case "${TRIPLE}" in
  *windows*) EXE=".exe" ;;
esac

mkdir -p "${OUT_DIR}"

# Entry shim: invoke the CLI's desktop-server subcommand.
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
cat > "${WORK}/entry.py" <<'PYEOF'
import sys
from contextseek.cli.main import main

if __name__ == "__main__":
    # Forward all args to: contextseek desktop-server ...
    sys.argv = ["contextseek", "desktop-server", *sys.argv[1:]]
    raise SystemExit(main())
PYEOF

# seekdb's native engine (pylibseekdb / libseekdb_python) has no Windows wheel,
# so it's absent there. Only bundle it where it's actually installed; otherwise
# `--collect-all`/`--copy-metadata` hard-fail on the missing package.
SEEKDB_ARGS=()
if "${PY}" -c "import pylibseekdb" >/dev/null 2>&1; then
  SEEKDB_ARGS+=(--collect-all pyseekdb --collect-all pylibseekdb --hidden-import libseekdb_python)
elif "${PY}" -c "import pyseekdb" >/dev/null 2>&1; then
  SEEKDB_ARGS+=(--collect-all pyseekdb)
fi

# LangChain/OpenAI embedding stack: bundle fully when present. These packages
# do import-time `importlib.metadata` version checks and tiktoken loads its
# `tiktoken_ext.openai_public` plugin dynamically, both of which PyInstaller's
# static analysis misses — so collect data + metadata and pin the hidden import.
EMBED_ARGS=()
if "${PY}" -c "import langchain_openai" >/dev/null 2>&1; then
  EMBED_ARGS+=(
    --collect-all langchain_openai --copy-metadata langchain-openai
    --collect-all langchain_core --copy-metadata langchain-core
  )
fi
if "${PY}" -c "import openai" >/dev/null 2>&1; then
  EMBED_ARGS+=(--collect-all openai --copy-metadata openai)
fi
if "${PY}" -c "import tiktoken" >/dev/null 2>&1; then
  EMBED_ARGS+=(
    --collect-all tiktoken --copy-metadata tiktoken
    --hidden-import tiktoken_ext.openai_public
  )
fi

CERTIFI_ARGS=()
if "${PY}" -c "import certifi" >/dev/null 2>&1; then
  CERTIFI_ARGS+=(--collect-all certifi --copy-metadata certifi)
fi

"${PY}" -m PyInstaller \
  --noconfirm --clean --onefile \
  --name "${NAME}" \
  --distpath "${WORK}/dist" \
  --workpath "${WORK}/build" \
  --specpath "${WORK}" \
  --collect-all contextseek \
  ${SEEKDB_ARGS[@]+"${SEEKDB_ARGS[@]}"} \
  ${EMBED_ARGS[@]+"${EMBED_ARGS[@]}"} \
  ${CERTIFI_ARGS[@]+"${CERTIFI_ARGS[@]}"} \
  "${WORK}/entry.py"

cp "${WORK}/dist/${NAME}${EXE}" "${OUT_DIR}/${NAME}-${TRIPLE}${EXE}"
echo "sidecar -> ${OUT_DIR}/${NAME}-${TRIPLE}${EXE}"
